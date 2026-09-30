from dotenv import load_dotenv
import logging
import requests
from bs4 import BeautifulSoup
import telebot
from telebot import types
import time
import threading
import re
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.firefox.options import Options as FirefoxOptions
import tkinter as tk
from tkinter import messagebox, scrolledtext
import sys
import datetime
import os
import json

# Настройка кодировки вывода
if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8')

DEFAULT_URL = 'https://www.auto24.ee/kasutatud/nimekiri.php?bn=2&a=100&g1=10&g2=15000&ad=1&ae=8&af=50&ssid=239646451&ak=0'
SAVED_URL_FILE = 'last_url.txt'
PRICES_DB_FILE = 'prices_db.json'
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not TOKEN:
    raise RuntimeError("Set BOT_TOKEN in .env before starting the application.")
CHECK_INTERVAL = 120  # 2 минуты 
BASE_URL = 'https://www.auto24.ee'

bot = telebot.TeleBot(TOKEN)
active_chats = set()
stop_thread = True
status_label = None
log_area = None
current_url = DEFAULT_URL
price_database = {}
bot_running = False

def log(message, style=None):
    """Вывод сообщения с возможностью форматирования"""
    if style == "header":
        formatted = f"\n{'='*50}\n{message}\n{'='*50}"
    elif style == "section":
        formatted = f"\n▶ {message}"
    elif style == "info":
        formatted = f"🌐 {message}"
    elif style == "count":
        formatted = f"🔍 {message}"
    elif style == "success":
        formatted = f"✅ {message}"
    elif style == "warning":
        formatted = f"⚠️ {message}"
    elif style == "bot":
        formatted = f"\n🤖 {message}"
    elif style == "search":
        formatted = f"\n🔎 {message}"
    elif style == "alert":
        formatted = f"📢 {message}"
    elif style == "stop":
        formatted = f"⏹ {message}"
    else:
        formatted = message
    
    print(formatted)
    if log_area:
        log_area.insert(tk.END, formatted + "\n")
        log_area.see(tk.END)

def format_price(price):
    return "{:,} €".format(price).replace(",", " ")

def load_saved_data():
    global current_url, price_database
    try:
        with open(SAVED_URL_FILE, 'r', encoding='utf-8') as f:
            current_url = f.read().strip()
    except Exception as e:
        log(f"Ошибка загрузки ссылки: {str(e)}", style="warning")
        current_url = DEFAULT_URL

    try:
        with open(PRICES_DB_FILE, 'r', encoding='utf-8') as f:
            price_database = json.load(f) if os.path.getsize(PRICES_DB_FILE) > 0 else {}
    except Exception as e:
        log(f"Ошибка загрузки базы цен: {str(e)}", style="warning")
        price_database = {}

def save_data():
    try:
        with open(SAVED_URL_FILE, 'w', encoding='utf-8') as f:
            f.write(current_url)
    except Exception as e:
        log(f"Ошибка сохранения URL: {str(e)}", style="warning")

    try:
        with open(PRICES_DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(price_database, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"Ошибка сохранения базы цен: {str(e)}", style="warning")

def setup_driver():
    """Настройка и инициализация Firefox WebDriver в фоновом режиме"""
    try:
        options = FirefoxOptions()
        options.add_argument("--headless")  # Включаем headless режим
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        
        driver = webdriver.Firefox(options=options)
        log("Firefox WebDriver успешно запущен (headless mode)", style="success")
        return driver
    except Exception as e:
        log(f"Ошибка запуска Firefox: {str(e)}", style="warning")
        return None

def parse_all_pages_auto(driver, start_url):
    """Парсинг всех страниц с объявлениями"""
    all_items = []
    current_url = start_url
    page_num = 1

    log("Начало парсинга", style="header")

    while True:
        log(f"Страница {page_num}", style="section")
        log(f"Загрузка: {current_url}", style="info")

        try:
            driver.get(current_url)
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".result-row"))
            )
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(1)
        except Exception as e:
            log(f"Ошибка загрузки страницы: {str(e)}", style="warning")
            break

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        items = soup.select('.result-row.item-odd.v-log, .result-row.item-even.v-log')
        log(f"Найдено объявлений: {len(items)}", style="count")

        if not items:
            log("Нет объявлений - прекращаем парсинг", style="warning")
            break

        new_items = 0
        for item in items:
            try:
                item_id = item.get('data-hsh', '').strip()
                if not item_id:
                    continue

                title = item.select_one('.description > .title > a.main')
                if not title:
                    continue
                    
                brand = title.select_one('span:first-child').text if title.select_one('span:first-child') else ""
                model = title.select_one('span.model').text if title.select_one('span.model') else ""
                engine = title.select_one('span.engine').text if title.select_one('span.engine') else ""
                full_title = f"{brand} {model} {engine}".strip()

                price = item.select_one('.pv .price')
                try:
                    price_value = int(re.sub(r'\D', '', price.text)) if price else 0
                except:
                    price_value = 0

                link = item.select_one('a.row-link')
                link_url = f"{BASE_URL}{link['href']}" if link and 'href' in link.attrs else ""

                image_url = None
                image = item.select_one('span.thumb')
                if image and 'style' in image.attrs:
                    match = re.search(r"url\('(.+?)'\)", image['style'])
                    if match:
                        image_url = match.group(1)
                        if image_url.startswith('//'):
                            image_url = 'https:' + image_url
                        elif image_url.startswith('/'):
                            image_url = BASE_URL + image_url

                def safe_extract(selector, default=""):
                    elem = item.select_one(selector)
                    return elem.text.strip() if elem else default

                year = safe_extract('.extra > span.year')
                mileage = safe_extract('.extra > span.mileage', '0 km').replace(' ', '')
                fuel = safe_extract('.extra > span.fuel:not(.sm-none)')
                transmission = safe_extract('.extra > span.transmission:not(.sm-none)')
                body = safe_extract('.extra > span.bodytype')
                drive = safe_extract('.extra > span.drive')

                if not fuel:
                    fuel_icon = item.select_one('.fuel_short_icon img')
                    if fuel_icon and 'fuel.png' in fuel_icon.get('src', ''):
                        fuel_char = fuel_icon.next_sibling.strip() if fuel_icon.next_sibling else ""
                        fuel = {'B': 'Bensiin', 'D': 'Diisel'}.get(fuel_char, fuel_char)
                
                if not transmission:
                    trans_icon = item.select_one('.transmission_short_icon img')
                    if trans_icon and 'gearbox.png' in trans_icon.get('src', ''):
                        trans_char = trans_icon.next_sibling.strip() if trans_icon.next_sibling else ""
                        transmission = {'A': 'Automaat', 'M': 'Manuaal'}.get(trans_char, trans_char)

                car_data = {
                    'id': item_id,
                    'title': full_title,
                    'price': price_value,
                    'link': link_url,
                    'image_url': image_url,
                    'year': year,
                    'mileage': mileage,
                    'fuel': fuel,
                    'transmission': transmission,
                    'body': body,
                    'drive': drive,
                    'last_checked': datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
                }

                all_items.append(car_data)
                new_items += 1

            except Exception as e:
                log(f"Ошибка обработки объявления: {str(e)}", style="warning")
                continue

        log(f"Добавлено: {new_items}", style="success")

        try:
            next_btn = driver.find_element(By.CSS_SELECTOR, 'a.input-link.item[rel="next"]')
            if not next_btn.is_displayed():
                log("Это последняя страница", style="stop")
                break

            current_url = next_btn.get_attribute('href')
            if not current_url:
                log("Нет ссылки на следующую страницу", style="stop")
                break

            page_num += 1
            time.sleep(2)

        except Exception as e:
            log("Кнопка следующей страницы не найдена", style="stop")
            break

    log(f"Итог: {page_num} стр., {len(all_items)} объяв.", style="header")
    return all_items

def check_price_changes(new_items):
    """Проверка изменений цен и новых объявлений"""
    changes = []
    for item in new_items:
        item_id = item['id']
        
        if item_id not in price_database:
            changes.append({
                'type': 'new',
                'id': item_id,
                'old_price': 0,
                'new_price': item['price'],
                'title': item['title'],
                'link': item['link'],
                'image_url': item['image_url'],
                'year': item['year'],
                'mileage': item['mileage'],
                'fuel': item['fuel'],
                'transmission': item['transmission'],
                'body': item['body'],
                'drive': item['drive']
            })
        elif price_database[item_id]['price'] != item['price']:
            changes.append({
                'type': 'price_change',
                'id': item_id,
                'old_price': price_database[item_id]['price'],
                'new_price': item['price'],
                'title': item['title'],
                'link': item['link'],
                'image_url': item['image_url'],
                'year': item['year'],
                'mileage': item['mileage'],
                'fuel': item['fuel'],
                'transmission': item['transmission'],
                'body': item['body'],
                'drive': item['drive']
            })
        
        price_database[item_id] = {
            'price': item['price'],
            'title': item['title'],
            'link': item['link'],
            'image_url': item['image_url'],
            'year': item['year'],
            'mileage': item['mileage'],
            'fuel': item['fuel'],
            'transmission': item['transmission'],
            'body': item['body'],
            'drive': item['drive'],
            'last_checked': item['last_checked']
        }
    
    save_data()
    return changes

def notify_price_changes(changes):
    """Отправка уведомлений об изменениях в телеграм"""
    for change in changes:
        for chat_id in active_chats:
            try:
                if change['type'] == 'new':
                    message_header = "🆕 *НОВОЕ ПРЕДЛОЖЕНИЕ!*"
                    price_info = f"Цена: {format_price(change['new_price'])}"
                else:
                    price_diff = change['new_price'] - change['old_price']
                    if price_diff > 0:
                        message_header = f"📈 *Цена ПОВЫШЕНА! (+{format_price(abs(price_diff))}*"
                    else:
                        message_header = f"📉 *Цена СНИЖЕНА! (-{format_price(abs(price_diff))}*"
                    
                    price_info = f"Было: {format_price(change['old_price'])}\nСтало: {format_price(change['new_price'])}"

                message = (
                    f"{message_header}\n\n"
                    f"*{change['title']}*\n"
                    f"{price_info}\n"
                    f"Год: {change['year']} | Пробег: {change['mileage']}\n"
                    f"Топливо: {change['fuel']} | КПП: {change['transmission']}\n"
                    f"Кузов: {change['body']} | Привод: {change['drive']}\n\n"
                    f"[Смотреть объявление]({change['link']})"
                )

                if change.get('image_url'):
                    try:
                        bot.send_photo(
                            chat_id,
                            change['image_url'],
                            caption=message,
                            parse_mode='Markdown'
                        )
                    except Exception as e:
                        log(f"Ошибка отправки фото: {str(e)}", style="warning")
                        bot.send_message(
                            chat_id,
                            message,
                            parse_mode='Markdown'
                        )
                else:
                    bot.send_message(
                        chat_id,
                        message,
                        parse_mode='Markdown'
                    )

            except Exception as e:
                log(f"Ошибка отправки: {str(e)}", style="warning")
                try:
                    bot.get_chat(chat_id)
                except:
                    active_chats.discard(chat_id)
                    log(f"Чат {chat_id} удален из активных", style="warning")

def monitor_prices():
    """Основной цикл мониторинга цен"""
    global bot_running
    while not stop_thread:
        try:
            driver = setup_driver()
            if not driver:
                time.sleep(CHECK_INTERVAL)
                continue
                
            log("Начало проверки цен...", style="search")
            all_items = parse_all_pages_auto(driver, current_url)
            
            if all_items:
                changes = check_price_changes(all_items)
                if changes:
                    log(f"Обнаружено изменений: {len(changes)}", style="alert")
                    notify_price_changes(changes)
                else:
                    log("Изменений нет")
            
            driver.quit()
            time.sleep(CHECK_INTERVAL)
            
        except Exception as e:
            log(f"Ошибка мониторинга: {str(e)}", style="warning")
            time.sleep(CHECK_INTERVAL)
    bot_running = False

@bot.message_handler(commands=['start'])
def send_welcome(message):
    """Обработчик команды /start"""
    active_chats.add(message.chat.id)
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn1 = types.KeyboardButton('Сменить ссылку')
    btn2 = types.KeyboardButton('Текущая ссылка')
    markup.add(btn1, btn2)
    
    bot.send_message(
        message.chat.id,
        f"🚗 Мониторинг цен Auto24\n\nТеперь вы будете получать уведомления о новых объявлениях и изменениях цен.\n\nТекущая ссылка:\n{current_url}",
        reply_markup=markup,
        parse_mode='Markdown'
    )

@bot.message_handler(func=lambda m: m.text == 'Сменить ссылку')
def request_new_url(message):
    """Запрос новой ссылки для мониторинга"""
    msg = bot.send_message(message.chat.id, "Введите новую ссылку для мониторинга (или 'default' для сброса):")
    bot.register_next_step_handler(msg, process_new_url)

def process_new_url(message):
    """Обработка новой ссылки для мониторинга"""
    global current_url
    new_url = message.text.strip()
    
    if new_url.lower() == 'default':
        current_url = DEFAULT_URL
        save_data()
        bot.send_message(message.chat.id, f"✅ Ссылка сброшена:\n{current_url}")
    elif new_url.startswith('http'):
        current_url = new_url
        save_data()
        bot.send_message(message.chat.id, f"✅ Ссылка обновлена:\n{current_url}")
    else:
        bot.send_message(message.chat.id, "❌ Неверный формат ссылки!")

@bot.message_handler(func=lambda m: m.text == 'Текущая ссылка')
def show_current_url(message):
    """Показ текущей ссылки для мониторинга"""
    bot.send_message(message.chat.id, f"🔗 Текущая ссылка:\n{current_url}")

def run_bot():
    """Запуск бота в отдельном потоке"""
    global bot_running
    log("Бот запущен и ожидает команд...", style="bot")
    bot_running = True
    while bot_running:
        try:
            bot.polling(none_stop=True, interval=1, timeout=30)
        except Exception as e:
            log(f"Ошибка бота: {str(e)}", style="warning")
            if "Conflict" in str(e):
                log("Перезапуск бота из-за конфликта...", style="warning")
                try:
                    bot.stop_polling()
                except:
                    pass
                time.sleep(5)
            else:
                time.sleep(15)

def create_gui():
    global status_label, log_area
    root = tk.Tk()
    root.title("Auto24 Price Monitor")
    root.geometry("600x450")

    tk.Label(root, text="Управление мониторингом цен", font=('Arial', 12)).pack(pady=10)
    btn_frame = tk.Frame(root)
    btn_frame.pack(pady=10)
    start_btn = tk.Button(btn_frame, text="Запустить", command=start_monitoring, bg="green", fg="white", width=12)
    start_btn.pack(side=tk.LEFT, padx=5)
    stop_btn = tk.Button(btn_frame, text="Остановить", command=stop_monitoring, bg="red", fg="white", width=12)
    stop_btn.pack(side=tk.LEFT, padx=5)
    status_label = tk.Label(root, text="Статус: НЕ АКТИВЕН", fg="red", font=('Arial', 10))
    status_label.pack(pady=10)
    log_area = scrolledtext.ScrolledText(root, wrap=tk.WORD, width=70, height=20)
    log_area.pack(pady=5)
    tk.Label(root, text=f"Текущая ссылка:\n{current_url}", font=('Arial', 8), wraplength=580).pack(pady=5)
    tk.Label(root, text="Для управления отправьте /start в боте", font=('Arial', 8)).pack(pady=5)
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()

def start_monitoring():
    global stop_thread
    if not stop_thread:
        messagebox.showwarning("Внимание", "Мониторинг уже запущен!")
        return
    stop_thread = False
    load_saved_data()
    threading.Thread(target=monitor_prices, daemon=True).start()
    if status_label:
        status_label.config(text="Статус: АКТИВЕН", fg="green")
    messagebox.showinfo("Информация", "Мониторинг цен запущен!")

def stop_monitoring():
    global stop_thread
    stop_thread = True
    if status_label:
        status_label.config(text="Статус: НЕ АКТИВЕН", fg="red")
    messagebox.showinfo("Информация", "Мониторинг остановлен")

def on_close():
    global bot_running, stop_thread
    stop_thread = True
    bot_running = False
    try:
        bot.stop_polling()
    except:
        pass
    if status_label:
        status_label.master.destroy()

if __name__ == '__main__':
    log("\n=== Auto24 Price Monitor ===", style="header")
    load_saved_data()
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    create_gui()