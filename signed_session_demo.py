import hmac
import hashlib
import base64
import json

SECRET_KEY = b"my_super_secret_key_12345"

def create_session(data):
    json_data = json.dumps(data).encode('utf-8')
    payload = base64.urlsafe_b64encode(json_data).decode('utf-8')
    
    signature_bytes = hmac.new(SECRET_KEY, payload.encode('utf-8'), hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(signature_bytes).decode('utf-8')
    
    return f"{payload}.{signature}"

def verify_session(session_token):
    try:
        payload, signature = session_token.split('.')
        
        expected_signature_bytes = hmac.new(SECRET_KEY, payload.encode('utf-8'), hashlib.sha256).digest()
        expected_signature = base64.urlsafe_b64encode(expected_signature_bytes).decode('utf-8')
        
        if not hmac.compare_digest(signature, expected_signature):
            return False, "Ошибка: Подпись неверна! Данные были изменены!"
            
        json_data = base64.urlsafe_b64decode(payload.encode('utf-8')).decode('utf-8')
        return True, json.loads(json_data)
    except Exception as e:
        return False, f"Ошибка парсинга сессии: {str(e)}"

print("=== 1. Создаем оригинальную сессию для обычного пользователя ===")
original_data = {"user_id": 105, "role": "user"}
token = create_session(original_data)
print(f"Токен сессии:\n{token}\n")

print("=== 2. Декодируем payload без секретного ключа (данные открыты) ===")
payload_part = token.split('.')[0]
decoded = base64.urlsafe_b64decode(payload_part.encode('utf-8')).decode('utf-8')
print(f"Прочитанные данные: {decoded}\n")

print("=== 3. Проверяем валидный токен на сервере ===")
success, result = verify_session(token)
print(f"Результат проверки: {result}\n")

print("=== 4. Пытаемся изменить данные (меняем роль на 'admin') ===")
fake_data = {"user_id": 105, "role": "admin"}
fake_payload = base64.urlsafe_b64encode(json.dumps(fake_data).encode('utf-8')).decode('utf-8')
old_signature = token.split('.')[1]
fake_token = f"{fake_payload}.{old_signature}"
print(f"Сфальсифицированный токен:\n{fake_token}")

success, result = verify_session(fake_token)
print(f"Результат проверки фальшивки: {result}\n")
