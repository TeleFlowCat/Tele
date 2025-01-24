
import streamlit as st
from telethon import TelegramClient, errors, events, sync
import asyncio
import datetime
import ntplib
import socket
import sys
import os
import logging
import time
import glob
import threading
import queue

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

API_ID = 23800218
API_HASH = "13fb6529163b3880b3974f8d035fdbab"
SESSIONS_DIR = "sessions"
SPAM_DIR = "spam"
#SESSION_FILE = 'my_session'  # Название файла сессии
CHATS_AVAILABLE_MESSAGE = "Доступные чаты:"
SELECT_CHAT_PROMPT = "Выберите номера чатов через пробел (например: 1 3 5): "
MESSAGE_COUNT_PROMPT = "Введите количество сообщений для сканирования: "
USERNAMES_SAVED_MESSAGE = "Сохранено {count} юзернеймов в usernames.txt"
ACCESS_BLOCKED_MESSAGE = "Доступ к функционалу заблокирован. Нажмите Enter, чтобы закрыть программу..."
CLOSE_PROGRAM_MESSAGE = "Нажмите Enter, чтобы закрыть программу..."


def get_ntp_time():
    """Получает текущее время с NTP сервера."""
    ntp_client = ntplib.NTPClient()
    try:
        response = ntp_client.request('pool.ntp.org', version=3)
        return datetime.datetime.fromtimestamp(response.tx_time).date()
    except (ntplib.NTPException, socket.gaierror):
        st.warning("Не удалось получить время с NTP сервера. Используется системное время.")
        return datetime.date.today()
    except Exception as e:
        st.error(f"Неожиданная ошибка при получении времени: {e}")
        return None

async def authenticate_telegram(api_id, api_hash, session_file):
    client = TelegramClient(os.path.join(SESSIONS_DIR, session_file), api_id, api_hash)
    try:
        await client.connect()
        if not client.is_connected():
           st.error("Не удалось подключиться к Telegram API.")
           return None
    except Exception as e:
        st.error(f"Произошла ошибка при подключении: {e}")
        return None

    if await client.is_user_authorized():
         logging.debug("Пользователь уже авторизован")
         return client
    phone_number = st.text_input("Введите номер телефона (в формате +7...):", key="phone_number")

    if phone_number:
        try:
            sent_code = await client.send_code_request(phone_number)
            logging.debug("Код подтверждения отправлен")
            st.session_state['phone_code_hash'] = sent_code.phone_code_hash # Store the phone_code_hash
        except errors.PhoneNumberInvalidError:
            st.error("Неверный номер телефона.")
            return None
        except Exception as e:
            st.error(f"Ошибка при отправке кода подтверждения: {e}")
            return None
        
        code_input = st.text_input("Введите код подтверждения:", key="code_input")
        if code_input:
            try:
                phone_code_hash = st.session_state.get('phone_code_hash') # Retrieve the phone_code_hash
                if not phone_code_hash:
                    st.error("phone_code_hash отсутствует. Пожалуйста, запросите код повторно.")
                    return None
                await client.sign_in(phone_number, code_input, phone_code_hash=phone_code_hash)
                st.success("Авторизация прошла успешно!")
                logging.debug("Авторизация без 2FA прошла успешно")
                return client
            except errors.SessionPasswordNeededError:
               st.error("Требуется пароль 2FA")
               password_input = st.text_input("Введите пароль 2FA:", key="password_input")
               if password_input:
                   try:
                      await client.sign_in(password=password_input)
                      st.success("Авторизация с 2FA прошла успешно!")
                      logging.debug("Авторизация с 2FA прошла успешно")
                      return client
                   except errors.PasswordHashInvalidError:
                       st.error("Неверный пароль 2FA")
                       return None
                   except errors.PasswordIncorrectError:
                       st.error("Неверный пароль 2FA")
                       os.remove(os.path.join(SESSIONS_DIR, session_file) + ".session") # Удаляем старую сессию.
                       logging.debug("Ошибка при авторизации с 2FA: Неверный пароль, удалена старая сессия")
                       return None
                   except Exception as e:
                       st.error(f"Ошибка при авторизации с 2FA: {e}")
                       logging.debug(f"Ошибка при авторизации с 2FA: {e}")
                       return None
            except errors.SessionExpiredError:
                 st.error("Срок действия сессии истек. Пожалуйста, авторизуйтесь снова")
                 os.remove(os.path.join(SESSIONS_DIR, session_file) + ".session") # Удаляем старую сессию, можно не делать.
                 logging.debug("Срок действия сессии истек, удалена старая сессия")
                 return None
            except errors.PhoneCodeInvalidError:
                st.error("Неверный код подтверждения.")
                logging.debug("Неверный код подтверждения")
                return None
            except Exception as e:
                st.error(f"Ошибка при авторизации: {e}")
                logging.debug(f"Ошибка при авторизации: {e}")
                return None
    
    return None # Возвращаем None, если авторизация не завершена

async def fetch_usernames(client, selected_chats, message_count):
    try:
        user_names = set()
        for dialog in selected_chats:
            try:
                chat_id = dialog.id
                async for message in client.iter_messages(chat_id, limit=message_count):
                    if message.from_id:
                        sender = await client.get_entity(message.from_id)
                        if sender.username:
                            user_names.add(sender.username)
            except errors.FloodWaitError as e:
                st.error(f"Слишком много запросов к Telegram API, подождите {e.seconds} секунд.")
                return None
            except errors.RPCError as e:
                st.error(f"Ошибка при получении сообщений из чата: {e}")
                return None
        
        os.makedirs(SPAM_DIR, exist_ok=True)
        with open(os.path.join(SPAM_DIR, "usernames.txt"), "w") as file:
                for username in user_names:
                    file.write(f"@{username}\n")

        st.success(USERNAMES_SAVED_MESSAGE.format(count=len(user_names)))
        return list(user_names)
    except Exception as e:
            st.error(f"Неожиданная ошибка: {e}")
            return None

async def get_dialogs(client):
     dialogs = []
     try:
       async for dialog in client.iter_dialogs(limit=50):
           dialogs.append(dialog)
     except Exception as e:
          st.error(f"Не удалось получить список чатов {e}")
          return []

     dialogs.sort(key=lambda d: d.name.lower())
     return dialogs

async def check_and_send_message(client, username, text_to_send, blacklist, updated_usernames, sent_count):
    """Асинхронная функция для проверки и отправки сообщения."""
    
    if username in blacklist:
        print(f"Пользователь {username} в черном списке. Удаляем из базы.")
        updated_usernames.remove(username)
        return False, updated_usernames, sent_count

    try:
        # Получение пользователя
        user = await client.get_entity(username)

        # Проверка наличия сообщений в чате с пользователем
        chat_exists = False
        async for msg in client.iter_messages(user, limit=1):
            print(f"Чат с {username} уже содержит сообщения. Добавляем в черный список и удаляем из базы.")
            blacklist.add(username)
            updated_usernames.remove(username)
            chat_exists = True
            break
        
        if chat_exists:
            return False, updated_usernames, sent_count
        
        # Если сообщений нет, отправляем сообщение
        await client.send_message(user, text_to_send)
        print(f"Сообщение отправлено: {username}")
        sent_count += 1
        blacklist.add(username)  # Добавление в блеклист только после успешной отправки
        updated_usernames.remove(username)
        return True, updated_usernames, sent_count
    except Exception as e:
        print(f"Не удалось обработать пользователя {username}: {e}")
        return False, updated_usernames, sent_count
    
async def spam_messages(client, delay, max_messages):
    try:
        # Получение сообщения из избранного
        me = await client.get_me()
        async for message in client.iter_messages(me.id, limit=1):  # Последнее сообщение
            if message.text:
                text_to_send = message.text
                break
        else:
            print("В Избранном нет текста для отправки.")
            return

        # Чтение юзернеймов из файла usernames.txt
        try:
            with open(os.path.join(SPAM_DIR, "usernames.txt"), "r") as file:
                usernames = [line.strip() for line in file if line.strip()]
        except FileNotFoundError:
            print("Файл usernames.txt не найден.")
            return

        # Загрузка или создание блеклиста
        blacklist_file = os.path.join(SPAM_DIR, "blacklist.txt")
        blacklist = set()
        if os.path.exists(blacklist_file):
            with open(blacklist_file, "r") as file:
                blacklist = set(line.strip() for line in file)
        else:
            os.makedirs(SPAM_DIR, exist_ok=True)
            open(blacklist_file, 'a').close()  # Создаем пустой файл если не существует

        # Рассылка сообщений
        print("Начало рассылки...")
        sent_count = 0
        updated_usernames = usernames[:]

        for username in usernames:
            if max_messages > 0 and sent_count >= max_messages:
                print("Достигнут лимит отправки сообщений.")
                break
            
            success, updated_usernames, sent_count = await check_and_send_message(client, username, text_to_send, blacklist, updated_usernames, sent_count)
            
            time.sleep(delay)

        # Обновление файла usernames.txt, чтобы оставить только неотправленные юзернеймы
        with open(os.path.join(SPAM_DIR, "usernames.txt"), "w") as file:
            file.writelines(f"{u}\n" for u in updated_usernames)

        # Обновление блеклиста
        with open(blacklist_file, "w") as file:
            for user in blacklist:
                file.write(f"{user}\n")


        print("\nРассылка успешно завершена.")
        print(f"Отправлено сообщений всего: {sent_count}")
        st.success(f"Рассылка завершена, отправлено {sent_count} сообщений")


    except Exception as e:
        print(f"Произошла ошибка: {e}")
        st.error(f"Произошла ошибка: {e}")

async def check_spamblock(client):
    """Проверяет наличие спамблока."""
    try:
         spambot = await client.get_entity("@spambot")
         await client.send_message(spambot, "/start")
         await asyncio.sleep(3)
         async for message in client.iter_messages(spambot, limit=1):
              if message.text:
                  if "Good news, no limits are currently applied to your account. You’re free as a bird!" in message.text or "Ваш аккаунт свободен от каких-либо ограничений." in message.text:
                    return "нет"
                  else:
                    return "да"
         return "да" # Если не было найдено сообщение, считаем что спамблок есть.
    except Exception as e:
           logging.error(f"Ошибка при проверке спам блока: {e}")
           return "неизвестно"


async def check_session_validity(api_id, api_hash, session_file):
    """Проверяет валидность сессии и возвращает данные аккаунта."""
    client = TelegramClient(os.path.join(SESSIONS_DIR, session_file), api_id, api_hash)
    try:
        await client.connect()
        if not client.is_connected():
            return {"status": "error", "message": "Не удалось подключиться к Telegram API", "session": session_file}
        
        if await client.is_user_authorized():
            me = await client.get_me()
            username = f"@{me.username}" if me.username else "отсутствует"
            premium = "да" if me.premium else "нет"
            spamblock = await check_spamblock(client) # Вызываем функцию для проверки спам блока.
            return {"status": "ok", "session": session_file, "first_name": me.first_name, "last_name": me.last_name, "phone": me.phone, "username": username, "premium": premium, "id": me.id, "spamblock": spamblock}
        else:
            return {"status": "invalid", "session": session_file, "message": "Сессия не авторизована"}
    
    except Exception as e:
         return {"status": "error", "message": str(e), "session": session_file}
    finally:
        await client.disconnect()

def send_code_request_task(session_name, api_id, api_hash, phone_number, result_queue):
    """Запрашивает код подтверждения (в потоке)."""
    from telethon.sync import TelegramClient
    import asyncio
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client = TelegramClient(os.path.join(SESSIONS_DIR, session_name), api_id, api_hash)
        client.connect()
        if not client.is_connected():
            result_queue.put("Не удалось подключиться к Telegram API.")
            return
        try:
            sent_code = client.send_code_request(phone_number)
            result_queue.put(("code_sent", sent_code.phone_code_hash)) # Return the phone_code_hash
        except Exception as e:
            result_queue.put(f"Ошибка при отправке запроса кода: {e}")
        finally:
            client.disconnect()

    except Exception as e:
        result_queue.put(f"Ошибка при подключении или запросе кода: {e}")


def submit_code_task(session_name, api_id, api_hash, phone_number, code_input, phone_code_hash, result_queue):
    """Подтверждает код и авторизует аккаунт (в потоке)."""
    from telethon.sync import TelegramClient
    import asyncio
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client = TelegramClient(os.path.join(SESSIONS_DIR, session_name), api_id, api_hash)
        client.connect()
        if not client.is_connected():
            result_queue.put("Не удалось подключиться к Telegram API.")
            return

        try:
            client.sign_in(phone_number, code_input, phone_code_hash=phone_code_hash)
            result_queue.put("success")
        except Exception as e:
            result_queue.put(f"Ошибка при авторизации: {e}")
        finally:
            client.disconnect()

    except Exception as e:
        result_queue.put(f"Ошибка при подключении или авторизации: {e}")


async def session_manager(api_id, api_hash):
    """Отображает интерфейс менеджера аккаунтов."""
    st.title("Менеджер аккаунтов")

    # Кнопка "Добавить аккаунт"
    if st.button("Добавить аккаунт"):
        st.session_state['add_account'] = True # Устанавливаем флаг

    if 'add_account' in st.session_state and st.session_state['add_account']:
        #  Форма для добавления аккаунта
        add_account(api_id, api_hash)

    session_files = glob.glob(os.path.join(SESSIONS_DIR, "*.session"))
    session_files = [os.path.basename(f) for f in session_files]

    if not session_files:
        st.warning("Нет доступных сессий.")
        return

    if "session_info" not in st.session_state:
        st.session_state["session_info"] = {}
    session_info = st.session_state["session_info"]

    for session_file in session_files:
        if session_file not in session_info:
            session_info[session_file] = {"expanded": False, "valid": False, "name": None, "phone": None, "username": None, "premium": None, "id": None, "spamblock": None, "error": None}

    for session_file in session_files:
        col1, col2, col3 = st.columns([6, 1, 1])
        with col1:
            st.write(f"**{session_file}**")
            if session_info[session_file]["expanded"]:
                if session_info[session_file]["valid"]:
                   st.markdown(f'<div style="margin-left: 20px;">Имя аккаунта: {session_info[session_file]["name"]}<br>Номер телефона: {session_info[session_file]["phone"]}<br>Юзернейм: {session_info[session_file]["username"]}<br>Telegram Premium: {session_info[session_file]["premium"]}<br>ID профиля: {session_info[session_file]["id"]}<br>Спамблок: {session_info[session_file]["spamblock"]}</div>', unsafe_allow_html=True)
                   st.success("Сессия валидна")
                elif session_info[session_file]["error"]:
                    st.markdown(f'<div style="margin-left: 20px;">{session_info[session_file]["error"]}</div>', unsafe_allow_html=True)
        with col2:
            if st.button("🗑️", key=f"delete_{session_file}"):
                try:
                    os.remove(os.path.join(SESSIONS_DIR, session_file))
                    st.success(f"Сессия {session_file} удалена.")
                    del session_info[session_file]
                    st.experimental_rerun()
                except Exception as e:
                    st.error(f"Не удалось удалить сессию {session_file}: {e}")
        with col3:
            if st.button("🔍", key=f"check_{session_file}"):
                with st.spinner(f"Проверка {session_file}..."):
                    result = await check_session_validity(api_id, api_hash, session_file)
                    if result["status"] == "ok":
                        if result["last_name"] is not None:
                             full_name = f"{result['first_name']} {result['last_name']}"
                        else:
                             full_name = f"{result['first_name']}"
                        session_info[session_file]["valid"] = True
                        session_info[session_file]["name"] = full_name
                        session_info[session_file]["phone"] = result["phone"]
                        session_info[session_file]["username"] = result["username"]
                        session_info[session_file]["premium"] = result["premium"]
                        session_info[session_file]["id"] = result["id"]
                        session_info[session_file]["spamblock"] = result["spamblock"]
                        session_info[session_file]["error"] = None
                    elif result["status"] == "invalid":
                        session_info[session_file]["valid"] = False
                        session_info[session_file]["name"] = None
                        session_info[session_file]["phone"] = None
                        session_info[session_file]["username"] = None
                        session_info[session_file]["premium"] = None
                        session_info[session_file]["id"] = None
                        session_info[session_file]["spamblock"] = None
                        session_info[session_file]["error"] = f'Невалидная сессия: {result["message"]}'
                    else:
                        session_info[session_file]["valid"] = False
                        session_info[session_file]["name"] = None
                        session_info[session_file]["phone"] = None
                        session_info[session_file]["username"] = None
                        session_info[session_file]["premium"] = None
                        session_info[session_file]["id"] = None
                        session_info[session_file]["spamblock"] = None
                        session_info[session_file]["error"] = f'Ошибка: {result["message"]}'
                    session_info[session_file]["expanded"] = True

    if st.button("Проверить все на валидность"):
       with st.spinner("Проверка всех сессий..."):
           for session_file in session_files:
                result = await check_session_validity(api_id, api_hash, session_file)
                if result["status"] == "ok":
                    if result["last_name"] is not None:
                        full_name = f"{result['first_name']} {result['last_name']}"
                    else:
                        full_name = f"{result['first_name']}"
                    st.write(f"**{session_file}**")
                    st.markdown(f'<div style="margin-left: 20px;">Имя аккаунта: {full_name}<br>Номер телефона: {result["phone"]}<br>Юзернейм: {result["username"]}<br>Telegram Premium: {result["premium"]}<br>ID профиля: {result["id"]}<br>Спамблок: {result["spamblock"]}</div>', unsafe_allow_html=True)
                    st.success("Сессия валидна")
                elif result["status"] == "invalid":
                    st.write(f"**{session_file}**")
                    st.markdown(f'<div style="margin-left: 20px;">Невалидная сессия: {result["message"]}</div>', unsafe_allow_html=True)
                else:
                    st.write(f"**{session_file}**")
                    st.markdown(f'<div style="margin-left: 20px;">Ошибка: {result["message"]}</div>', unsafe_allow_html=True)

def add_account(api_id, api_hash):
    """Добавляет новый аккаунт через интерфейс."""
    st.subheader("Добавить новый аккаунт")
    session_name = st.text_input("Имя новой сессии:", "new_session")
    phone_number = st.text_input("Номер телефона (+79999999999):", "")

    if "code_sent" not in st.session_state:
        st.session_state.code_sent = False
    if "auth_status" not in st.session_state:
        st.session_state.auth_status = None
    if 'phone_code_hash' not in st.session_state:
        st.session_state['phone_code_hash'] = None
    
    result_queue = queue.Queue()

    if st.button("Запросить код", disabled=st.session_state.code_sent):
         if not phone_number:
             st.warning("Введите номер телефона.")
             return
         if not api_id or not api_hash:
             st.warning("Введите API ID и API Hash.")
             return

         thread = threading.Thread(target=send_code_request_task, args=(session_name, api_id, api_hash, phone_number, result_queue))
         thread.start()
         thread.join()

         result = result_queue.get()
         if isinstance(result, tuple) and result[0] == "code_sent":
            st.session_state.code_sent = True
            st.session_state['phone_code_hash'] = result[1]  # Store phone_code_hash in session state
         else:
             st.error(result)
             st.session_state.code_sent = False

    if st.session_state.code_sent:
        code_input = st.text_input("Введите код подтверждения:")
        if st.button("Подтвердить код"):
           if not code_input:
              st.warning("Введите код подтверждения.")
              return
           if not api_id or not api_hash:
                st.warning("Введите API ID и API Hash.")
                return
           phone_code_hash = st.session_state.get('phone_code_hash')
           if not phone_code_hash:
               st.error("phone_code_hash отсутствует. Пожалуйста, запросите код повторно.")
               return

           thread = threading.Thread(target=submit_code_task, args=(session_name, api_id, api_hash, phone_number, code_input, phone_code_hash, result_queue))
           thread.start()
           thread.join()

           result = result_queue.get()
           if result == "success":
                st.success(f"Аккаунт {session_name} успешно авторизован!")
                st.session_state.auth_status = "success"
                st.session_state.code_sent = False  # Сброс состояния
                st.session_state['phone_code_hash'] = None # Reset this too
                st.session_state.auth_status = None
                #st.experimental_rerun() #  Перезагрузка для отображения нового аккаунта
                st.rerun()
           elif result == "failed":
                st.error("Неверный код или ошибка авторизации.")
                st.session_state.auth_status = "failed"
           else:
               st.error(result)
               st.session_state.auth_status = "failed"

    if st.session_state.auth_status == "success":
        st.success(f"Аккаунт {session_name} успешно авторизован!")
    elif st.session_state.auth_status == "failed":
        st.error(f"Аккаунт {session_name} не авторизован.")



async def main():
    st.title("TeleFlow Private")
    st.sidebar.title("Меню")

    api_id = st.sidebar.number_input("Введите API ID", value=API_ID)
    api_hash = st.sidebar.text_input("Введите API HASH", value=API_HASH)
    
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    session_files = glob.glob(os.path.join(SESSIONS_DIR, "*.session"))
    session_files = [os.path.basename(f) for f in session_files] # Для отображения в UI
    if session_files:
        session_file = st.sidebar.selectbox("Выберите файл сессии:", session_files)
    else:
        session_file = 'my_session.session'
        st.sidebar.warning("Не найдено файлов сессии в папке 'sessions'. Будет использован 'my_session.session'")
    
    
    menu_selection = st.sidebar.selectbox("Выберите действие:", ["Парсинг", "Спам", "Менеджер аккаунтов"])
    
    if menu_selection == "Менеджер аккаунтов":
        await session_manager(api_id, api_hash)
        return

    client = await authenticate_telegram(api_id, api_hash, session_file)
    if not client:
        st.stop()
    
    if menu_selection == "Парсинг":
        dialogs = await get_dialogs(client)
        if not dialogs:
            st.stop()

        chat_names = [f"{i + 1}: {dialog.name}" for i, dialog in enumerate(dialogs)]
        selected_chat_names = st.multiselect("Выберите чаты:", chat_names)
        selected_chats = [dialogs[chat_names.index(name)] for name in selected_chat_names]

        message_count = st.number_input(MESSAGE_COUNT_PROMPT, min_value=1, value=10)
        
        if st.button("Начать сканирование"):
             if not selected_chats:
                st.error("Пожалуйста, выберите чаты.")
             else:
                with st.spinner("Сканирование чатов..."):
                   usernames = await fetch_usernames(client, selected_chats, message_count)
                   if usernames:
                         st.write("Список юзернеймов:")
                         st.write(usernames)
                         
    elif menu_selection == "Спам":
       delay = st.number_input("Введите задержку между отправками (в секундах): ", min_value=0.1, value=1.0)
       max_messages = st.number_input("Введите количество сообщений для отправки (0 для отправки всем): ", min_value=0, value=0)
       
       if st.button("Начать спам"):
           with st.spinner("Отправка сообщений..."):
              await spam_messages(client, delay, max_messages)


if __name__ == '__main__':
    asyncio.run(main())
