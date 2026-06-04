import json
import os
import time
import sys
import io
from minio import Minio
from kafka import KafkaProducer
from datetime import datetime

# Configuracion
# Configuración MinIO
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
minio_client = Minio(
    MINIO_ENDPOINT,
    access_key="admin",
    secret_key="adminpassword",
    secure=False
)

# Crear bucket para el Data Lake si no existe
if not minio_client.bucket_exists("datalake"):
    minio_client.make_bucket("datalake")
    print("Bucket 'datalake' creado en MinIO")

KAFKA_BROKER = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
TOPIC = os.getenv("TOPIC", "sec-alerts")
DATA_DIR = "/data"

# Archivos a monitorear
FILES_TO_WATCH = {
    "alerts.json": "ossec",
    "alert_json.txt": "snort"
}

# Archivo para guardar la ultima posicion leida
STATE_FILE = "/tmp/producer_state.json"

class StreamingProducer:
    def __init__(self):
        self.producer = None
        self.file_positions = {}
        self.load_state()
    
    def connect_kafka(self):
        """Conectar a Kafka con reintentos"""
        max_retries = 10
        for i in range(max_retries):
            try:
                self.producer = KafkaProducer(
                    bootstrap_servers=KAFKA_BROKER,
                    value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                    acks=1,
                    retries=3
                )
                print(f"Conectado a Kafka en {KAFKA_BROKER}")
                return True
            except Exception as e:
                print(f"Intento {i+1}/{max_retries}: {e}")
                time.sleep(5)
        return False
    
    def load_state(self):
        """Cargar ultima posicion leida de cada archivo"""
        try:
            with open(STATE_FILE, 'r') as f:
                self.file_positions = json.load(f)
                print(f"Estado cargado: {self.file_positions}")
        except:
            self.file_positions = {}
            print("Iniciando desde cero")
    
    def save_state(self):
        """Guardar posicion actual"""
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(self.file_positions, f)
        except:
            pass
    
    def get_file_position(self, filepath):
        """Obtener ultima posicion leida del archivo"""
        return self.file_positions.get(filepath, 0)
    
    def update_file_position(self, filepath, position):
        """Actualizar posicion leida"""
        self.file_positions[filepath] = position
        self.save_state()
    
    def read_new_lines(self, filepath, source_type):
        """Leer lineas nuevas desde la ultima posicion"""
        if not os.path.exists(filepath):
            return 0
        
        current_pos = self.get_file_position(filepath)
        file_size = os.path.getsize(filepath)
        
        # Si el archivo se trunco (reinicio), empezar desde cero
        if current_pos > file_size:
            current_pos = 0
        
        lines_sent = 0
        raw_lines_buffer = []
        
        with open(filepath, 'r', encoding='utf-8') as f:
            f.seek(current_pos)
            
            for line in f:
                line = line.strip()
                if not line:
                    continue

                # Se guarda la linea cruda para almacenamiento en MinIO
                raw_lines_buffer.append(line)
                
                try:
                    # Parsear JSON
                    alert = json.loads(line)
                    alert['source'] = source_type
                    alert['stream_timestamp'] = datetime.now().isoformat()
                    
                    # Enviar a Kafka
                    self.producer.send(TOPIC, alert)
                    lines_sent += 1
                    
                    if lines_sent % 100 == 0:
                        print(f"   Enviadas {lines_sent} alertas de {source_type}")
                    
                except json.JSONDecodeError as e:
                    print(f"   Error JSON: {e}")
                except Exception as e:
                    print(f"   Error enviando: {e}")
            
            # Subir a MinIO si hubo líneas nuevas
            if raw_lines_buffer:
                self.upload_to_minio(source_type, raw_lines_buffer)

            # Actualizar posicion
            new_pos = f.tell()
            self.update_file_position(filepath, new_pos)
        
        if lines_sent > 0:
            print(f"   {source_type}: {lines_sent} nuevas alertas (posicion: {new_pos})")
        
        return lines_sent

    def upload_to_minio(self, source_type, lines_buffer):
        """Sube un bloque de logs crudos a MinIO organizados por fecha"""
        try:
            date_str = datetime.now().strftime("%Y/%m/%d")
            timestamp = datetime.now().strftime("%H%M%S")
            # Esto genera una ruta tipo: ossec/2026/06/03/raw_logs_171547.json
            object_name = f"{source_type}/{date_str}/raw_logs_{timestamp}.json"
            
            # Convertir la lista de strings a un flujo de bytes
            raw_data = "\n".join(lines_buffer).encode('utf-8')
            raw_stream = io.BytesIO(raw_data)
            
            minio_client.put_object(
                "datalake",
                object_name,
                data=raw_stream,
                length=len(raw_data),
                content_type="application/json"
            )
            print(f"   [Data Lake] {len(lines_buffer)} logs crudos subidos a MinIO: {object_name}")
        except Exception as e:
            print(f"   [Error Data Lake] No se pudo subir a MinIO: {e}")
    
    def run(self):
        """Bucle principal de monitoreo"""
        print(f"\nIniciando streaming desde {DATA_DIR}")
        print(f"Monitoreando archivos: {list(FILES_TO_WATCH.keys())}")
        print(f"Enviando a Kafka topic: {TOPIC}\n")
        
        if not self.connect_kafka():
            print("No se pudo conectar a Kafka")
            sys.exit(1)
        
        last_check = {}
        
        try:
            while True:
                for filename, source_type in FILES_TO_WATCH.items():
                    filepath = os.path.join(DATA_DIR, filename)
                    
                    # Verificar si el archivo existe
                    if not os.path.exists(filepath):
                        if filepath not in last_check or last_check[filepath] == 0:
                            print(f"Esperando archivo: {filename}")
                        last_check[filepath] = 0
                        continue
                    
                    # Verificar si el archivo cambio
                    current_size = os.path.getsize(filepath)
                    last_size = last_check.get(filepath, 0)
                    
                    if current_size != last_size:
                        print(f"\nDetectado cambio en {filename} (tamano: {current_size} bytes)")
                        new_lines = self.read_new_lines(filepath, source_type)
                        last_check[filepath] = current_size
                        
                        # Commit despues de cada lote
                        if new_lines > 0:
                            self.producer.flush()
                
                # Esperar antes de la siguiente verificacion
                time.sleep(2)  # Revisar cada 2 segundos
                
        except KeyboardInterrupt:
            print(f"\n\nDeteniendo producer...")
            self.producer.flush()
            self.producer.close()
            print("Producer detenido")

if __name__ == "__main__":
    print("=== Streaming Security Producer ===")
    print(f"Monitoreando: {DATA_DIR}")
    
    producer = StreamingProducer()
    producer.run()