import json
import os
import sys
import psycopg2
from psycopg2.extras import Json
from kafka import KafkaConsumer
from datetime import datetime

# Configuracion
KAFKA_BROKER = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
TOPIC = os.getenv("TOPIC", "sec-alerts")

DB_HOST = os.getenv("POSTGRES_HOST", "postgres")
DB_NAME = os.getenv("POSTGRES_DB", "security_warehouse")
DB_USER = os.getenv("POSTGRES_USER", "admin")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "admin123")

def get_db_connection():
    return psycopg2.connect(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASS
    )

def create_table_if_not_exists():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Verificar si la tabla existe, si no, crearla
    cur.execute("""
        CREATE TABLE IF NOT EXISTS security_alerts (
            id SERIAL PRIMARY KEY,
            source VARCHAR(20),
            alert_type VARCHAR(50),
            level INTEGER,
            rule_id VARCHAR(100),
            alert_timestamp TIMESTAMP,
            src_ip VARCHAR(45),
            dst_ip VARCHAR(45),
            protocol VARCHAR(10),
            action VARCHAR(20),
            message TEXT,
            hostname VARCHAR(100),
            full_log TEXT,
            location VARCHAR(100),
            stream_timestamp TIMESTAMP,
            raw_json JSONB,
            ingested_at TIMESTAMP DEFAULT NOW()
        )
    """)
    
    # Crear indices si no existen
    cur.execute("CREATE INDEX IF NOT EXISTS idx_alert_timestamp ON security_alerts(alert_timestamp)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_src_ip ON security_alerts(src_ip)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_level ON security_alerts(level)")
    
    conn.commit()
    cur.close()
    conn.close()
    print("Tabla security_alerts lista")

def parse_ossec_alert(alert):
    """Parsear alerta de OSSEC (rule es diccionario)"""
    if not isinstance(alert, dict):
        return None
    
    rule = alert.get('rule', {})
    if not isinstance(rule, dict):
        rule = {}
    
    level = rule.get('level')
    if level is None or not isinstance(level, int):
        level = 0
    
    # Parsear timestamp
    timestamp_str = alert.get('timestamp', '')
    alert_timestamp = None
    if timestamp_str and isinstance(timestamp_str, str):
        try:
            alert_timestamp = datetime.strptime(timestamp_str, "%Y %b %d %H:%M:%S")
        except:
            pass
    
    return {
        'source': 'ossec',
        'alert_type': alert.get('decoder', '') if isinstance(alert.get('decoder'), str) else '',
        'level': level,
        'rule_id': str(rule.get('sidid', '')) if rule.get('sidid') else '',
        'alert_timestamp': alert_timestamp,
        'hostname': alert.get('hostname', '') if isinstance(alert.get('hostname'), str) else '',
        'message': str(rule.get('comment', ''))[:500] if rule.get('comment') else '',
        'full_log': alert.get('full_log', '') if isinstance(alert.get('full_log'), str) else '',
        'location': alert.get('location', '') if isinstance(alert.get('location'), str) else '',
        'stream_timestamp': alert.get('stream_timestamp'),
        'raw_json': alert
    }

def parse_snort_alert(alert):
    """Parsear alerta de Snort (rule es STRING, no diccionario)"""
    if not isinstance(alert, dict):
        return None
    
    # Extraer IPs de "IP:PORT"
    src_ap = alert.get('src_ap', ':')
    dst_ap = alert.get('dst_ap', ':')
    
    src_ip = ''
    dst_ip = ''
    
    if isinstance(src_ap, str) and ':' in src_ap:
        src_ip = src_ap.split(':')[0]
    if isinstance(dst_ap, str) and ':' in dst_ap:
        dst_ip = dst_ap.split(':')[0]
    
    # Parsear timestamp de Snort
    timestamp_str = alert.get('timestamp', '')
    alert_timestamp = None
    if timestamp_str and isinstance(timestamp_str, str):
        try:
            # Formato: "06/02-15:28:57.819102"
            if '-' in timestamp_str:
                parts = timestamp_str.split('-')
                date_part = parts[0]  # "06/02"
                time_part = parts[1].split('.')[0]  # "15:28:57"
                month, day = date_part.split('/')
                timestamp_str_fixed = f"2026-{month}-{day} {time_part}"
                alert_timestamp = datetime.strptime(timestamp_str_fixed, "%Y-%m-%d %H:%M:%S")
        except Exception as e:
            print(f"      Warning Error timestamp: {e}")
    
    # Obtener rule_id (es un string, ej: "1:1000001:1")
    rule_id = alert.get('rule', '')
    if not isinstance(rule_id, str):
        rule_id = str(rule_id) if rule_id else ''
    
    # Obtener protocolo
    protocol = alert.get('proto', '')
    if not isinstance(protocol, str):
        protocol = str(protocol) if protocol else ''
    
    # Obtener accion
    action = alert.get('action', '')
    if not isinstance(action, str):
        action = str(action) if action else ''
    
    return {
        'source': 'snort',
        'protocol': protocol,
        'alert_timestamp': alert_timestamp,
        'src_ip': src_ip,
        'dst_ip': dst_ip,
        'rule_id': rule_id,
        'action': action,
        'level': 0,  # Snort no tiene nivel, usar 0
        'message': f"Protocol: {protocol} - Action: {action} - Rule: {rule_id}",
        'stream_timestamp': alert.get('stream_timestamp'),
        'raw_json': alert
    }

def insert_alert(alert):
    """Inserta alerta en PostgreSQL segun su tipo"""
    if not isinstance(alert, dict):
        print(f"      Alerta no es diccionario: {type(alert)}")
        return False
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Detectar tipo por la presencia de campos especificos
        # OSSEC tiene 'decoder' o 'rule' como diccionario
        # Snort tiene 'proto' o 'src_ap'
        
        is_ossec = False
        is_snort = False
        
        # Verificar si es OSSEC (rule es diccionario)
        if 'decoder' in alert:
            is_ossec = True
        elif 'rule' in alert and isinstance(alert.get('rule'), dict):
            is_ossec = True
        
        # Verificar si es Snort
        if 'proto' in alert or 'src_ap' in alert:
            is_snort = True
        
        if is_ossec:
            parsed = parse_ossec_alert(alert)
            if parsed is None:
                return False
            
            cur.execute("""
                INSERT INTO security_alerts 
                (source, alert_type, level, rule_id, alert_timestamp, hostname, message, full_log, location, stream_timestamp, raw_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                parsed['source'], parsed['alert_type'], parsed['level'],
                parsed['rule_id'], parsed['alert_timestamp'], parsed['hostname'],
                parsed['message'], parsed['full_log'], parsed['location'],
                parsed['stream_timestamp'], Json(parsed['raw_json'])
            ))
            print(f"      OSSEC guardado (Level: {parsed['level']})")
            
        elif is_snort:
            parsed = parse_snort_alert(alert)
            if parsed is None:
                return False
            
            cur.execute("""
                INSERT INTO security_alerts 
                (source, protocol, alert_timestamp, src_ip, dst_ip, rule_id, action, level, message, stream_timestamp, raw_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                parsed['source'], parsed['protocol'], parsed['alert_timestamp'],
                parsed['src_ip'], parsed['dst_ip'], parsed['rule_id'],
                parsed['action'], parsed['level'], parsed['message'],
                parsed['stream_timestamp'], Json(parsed['raw_json'])
            ))
            print(f"      Snort guardado ({parsed['protocol']} - {parsed['src_ip']}->{parsed['dst_ip']})")
        else:
            # Alerta desconocida, guardar como raw
            cur.execute("""
                INSERT INTO security_alerts (source, raw_json, stream_timestamp)
                VALUES (%s, %s, %s)
            """, ('unknown', Json(alert), datetime.now()))
            print(f"      Tipo desconocido guardado como raw")
        
        conn.commit()
        return True
        
    except Exception as e:
        print(f"      Error DB: {e}")
        conn.rollback()
        return False
    finally:
        cur.close()
        conn.close()

def main():
    print("=== Streaming Security Consumer ===")
    print(f"Kafka: {KAFKA_BROKER} | Topic: {TOPIC}")
    
    # Crear tabla si no existe
    create_table_if_not_exists()
    
    # Conectar a Kafka
    consumer = KafkaConsumer(
        TOPIC,
        bootstrap_servers=KAFKA_BROKER,
        auto_offset_reset='earliest',
        enable_auto_commit=True,
        group_id='security-streaming-group-v2',  # Nuevo group_id para reprocesar
        value_deserializer=lambda m: json.loads(m.decode('utf-8'))
    )
    
    print("Conectado a Kafka - Procesando streaming en tiempo real...\n")
    
    count = 0
    success = 0
    errors = 0
    
    try:
        for message in consumer:
            count += 1
            alert = message.value
            
            # Determinar tipo para el log
            if 'decoder' in alert:
                source = f"OSSEC (Lvl: {alert.get('rule', {}).get('level', '?')})"
            elif 'proto' in alert:
                source = f"Snort ({alert.get('proto', '?')})"
            else:
                source = "Unknown"
            
            print(f"Recibido #{count} - {source}")
            
            if insert_alert(alert):
                success += 1
            else:
                errors += 1
            
            if count % 100 == 0:
                print(f"\nProgreso: {count} procesadas, {success} exitosas, {errors} errores\n")
    
    except KeyboardInterrupt:
        print(f"\n\nConsumer detenido")
        print(f"Estadisticas finales: {success}/{count} exitosas")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nDetenido manualmente")
    except Exception as e:
        print(f"\nError fatal: {e}")
        import traceback
        traceback.print_exc()