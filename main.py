# main.py
"""
Основной файл Telegram-бота для турниров Brawl Stars
"""

import uvicorn
import asyncio
import logging
import random
from fastapi import FastAPI
from threading import Thread
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    Poll, Message, Chat, User, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    PollAnswerHandler, ContextTypes, MessageHandler, filters, ApplicationBuilder
)

from config import (
    BOT_TOKEN, OWNER_ID, USER_ROLES, TOURNAMENT_FORMATS,
    GAME_MODES, MESSAGES, EMOJIS, SCHEDULED_TOURNAMENT_HOURS, BOT_COMMANDS,
    MIN_TOURNAMENT_PARTICIPANTS
)
from database import DatabaseManager
from maps import map_manager

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Глобальные переменные
db = DatabaseManager()
active_tournaments = {}  # chat_id: tournament_data
active_polls = {}  # poll_id: poll_data
user_participation_tracker = {}  # chat_id: {user_id: bool} - отслеживание участия
match_scores = {}  # match_id: {'team1_wins': 0, 'team2_wins': 0} - счёт матчей

app = FastAPI()

@app.get("/healthz")
async def health_check():
    return {"status": "ok"}

def run_web():
    uvicorn.run(app, host="0.0.0.0", port=8080)

# Вспомогательные функции для работы с Markdown
def escape_markdown_v2(text):
    """Экранирование специальных символов Markdown v2"""
    if not text:
        return ""
    
    text = str(text)
    # Символы, которые нужно экранировать в Markdown v2
    escape_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    
    for char in escape_chars:
        text = text.replace(char, f'\\{char}')
    
    return text

def clean_markdown(text):
    """Полное удаление Markdown символов"""
    if not text:
        return ""
    
    text = str(text)
    # Удаляем все потенциально проблемные символы
    problematic_chars = ['*', '_', '`', '[', ']', '(', ')', '~', '>', '#']
    
    for char in problematic_chars:
        text = text.replace(char, '')
    
    return text

def get_rank_by_xp(xp):
    """Определение ранга по опыту"""
    if xp >= 1000:
        return "👑 Легенда"
    elif xp >= 500:
        return "🏆 Мастер"
    elif xp >= 200:
        return "🎖️ Эксперт"
    elif xp >= 50:
        return "🥉 Любитель"
    else:
        return "🙋 Новичок"

def check_tournament_start_conditions(tournament):
    """Проверка условий для старта турнира с поддержкой до 6 команд"""
    format_type = tournament.get('format', '1v1')
    participants_count = len(tournament.get('participants', []))
    min_participants = MIN_TOURNAMENT_PARTICIPANTS.get(format_type, 2)
    
    if participants_count < min_participants:
        return False, f"Нужно минимум {min_participants} участников! (есть {participants_count})"
    
    if format_type == '2v2':
        # Для 2v2 проверяем, можем ли сформировать полные команды по 2 игрока
        full_teams = participants_count // 2
        if full_teams < 2:  # Минимум 2 команды для турнира
            needed_players = 4 - participants_count
            return False, f"Для 2v2 нужно минимум 4 игрока (2 команды). Не хватает: {needed_players}"
        
        # Максимум 6 команд (12 игроков)
        if participants_count > 12:
            return False, "Максимум 12 игроков для турнира 2v2 (6 команд)"
        
        # Если есть лишний игрок, предупреждаем
        if participants_count % 2 != 0:
            return False, f"Для 2v2 нужно четное количество игроков. Лишний игрок: 1"
            
    elif format_type == '3v3':
        # Для 3v3 проверяем команды по 3 игрока
        full_teams = participants_count // 3
        if full_teams < 2:  # Минимум 2 команды
            needed_players = 6 - participants_count
            return False, f"Для 3v3 нужно минимум 6 игроков (2 команды). Не хватает: {needed_players}"
        
        # Максимум 6 команд (18 игроков)
        if participants_count > 18:
            return False, "Максимум 18 игроков для турнира 3v3 (6 команд)"
        
        # Если участники не делятся на 3, предупреждаем
        remainder = participants_count % 3
        if remainder != 0:
            needed_to_complete = 3 - remainder
            return False, f"Для 3v3 участники должны делиться на 3. Не хватает до полной команды: {needed_to_complete}"
    
    return True, "Готов к старту!"

class TournamentBot:
    def __init__(self):
        self.db = db
    
    async def get_team_names(self, team, context, chat_id):
        """Получение имен участников команды"""
        names = []
        for user_id in team:
            try:
                user = await context.bot.get_chat_member(chat_id, user_id)
                username = user.user.username or user.user.first_name
                
                # Очищаем имя от Markdown символов для безопасности
                clean_username = clean_markdown(str(username))
                
                if user.user.username:
                    names.append(f"@{clean_username}")
                else:
                    names.append(clean_username)
            except Exception as e:
                logger.error(f"Error getting user {user_id}: {e}")
                names.append(f"User{user_id}")
        
        return " + ".join(names)

    async def show_current_round(self, query, context):
        """Показ текущего раунда"""
        chat_id = query.message.chat_id
        tournament = active_tournaments[chat_id]
        bracket = tournament.get('bracket', {})
        current_round = bracket.get('current_round', 'round_1')
        matches = bracket.get(current_round, [])
        
        if not matches:
            await self.finish_tournament(query, context)
            return
        
        # Определяем название раунда
        total_matches = len(matches)
        if total_matches == 1:
            round_name = "🏆 ФИНАЛ ТУРНИРА!"
        elif total_matches == 2:
            round_name = "🎯 ПОЛУФИНАЛ!"
        else:
            round_name = f"⚔️ Раунд {current_round.split('_')[1]}"
        
        matches_text = []
        keyboard = []
        
        for match in matches:
            if match.get('winner') is None:
                team1_names = await self.get_team_names(match['team1'], context, chat_id)
                team2_names = await self.get_team_names(match['team2'], context, chat_id)
                
                # Получаем счёт матча
                match_id = match.get('match_id', 0)
                score = match_scores.get(match_id, {'team1_wins': 0, 'team2_wins': 0})
                score_text = f"({score['team1_wins']}:{score['team2_wins']})"
                
                match_text = f"🟥 {team1_names} 🆚 🟦 {team2_names} {score_text}"
                matches_text.append(match_text)
                
                # Кнопки для выбора победителя (только для модераторов и выше)
                user = self.db.get_user(query.from_user.id)
                if user and user.get('role') in ['moderator', 'admin', 'owner'] or query.from_user.id == OWNER_ID:
                    keyboard.append([
                        InlineKeyboardButton(f"🏆 Команда 1", 
                                           callback_data=f"match_winner_{match_id}_team1"),
                        InlineKeyboardButton(f"🏆 Команда 2", 
                                           callback_data=f"match_winner_{match_id}_team2")
                    ])
        
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        
        # Формируем простое сообщение без сложного форматирования
        format_name = TOURNAMENT_FORMATS.get(tournament.get('format', '1v1'), tournament.get('format', '1v1'))
        
        message_text = f"{round_name}\n\n"
        message_text += f"Формат: {format_name}\n"
        message_text += f"Побед для прохождения: {tournament.get('wins_needed', 1)}\n"
        
        if tournament.get('selected_modes'):
            modes_list = ", ".join(tournament['selected_modes'])
            message_text += f"Режимы: {modes_list}\n"
        
        message_text += f"\nМатчи:\n"
        message_text += "\n".join(matches_text)
        
        # Отправляем без Markdown форматирования
        try:
            await query.edit_message_text(
                message_text,
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Error editing message: {e}")
            # Максимально простое сообщение
            simple_text = f"{round_name}\n\nМатчи:\n" + "\n".join(matches_text)
            try:
                await query.edit_message_text(
                    simple_text,
                    reply_markup=reply_markup
                )
            except Exception as final_error:
                logger.error(f"Final fallback failed: {final_error}")

    async def update_tournament_message(self, query, context):
        """Обновление сообщения турнира"""
        chat_id = query.message.chat_id
        tournament = active_tournaments[chat_id]
        
        keyboard = [
            [InlineKeyboardButton("✅ Участвовать", callback_data="join_tournament")],
            [InlineKeyboardButton("❌ Выйти", callback_data="leave_tournament")]
        ]
        
        # Проверяем условия для старта турнира
        can_start, status_message = check_tournament_start_conditions(tournament)
        
        # Добавляем кнопку старта для создателя, если условия соблюдены
        if can_start and query.from_user.id == tournament.get('creator'):
            keyboard.append([InlineKeyboardButton("🚀 Начать турнир!", callback_data="start_bracket")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Список участников
        participants_list = []
        for i, participant_id in enumerate(tournament.get('participants', []), 1):
            try:
                user = await context.bot.get_chat_member(chat_id, participant_id)
                username = user.user.username or user.user.first_name
                
                # Очищаем имя от потенциально проблемных символов
                clean_username = str(username).replace("*", "").replace("_", "").replace("`", "").replace("[", "").replace("]", "")
                
                if user.user.username:
                    participants_list.append(f"{i}. @{clean_username}")
                else:
                    participants_list.append(f"{i}. {clean_username}")
            except Exception as e:
                logger.error(f"Error getting participant {participant_id}: {e}")
                participants_list.append(f"{i}. Пользователь {participant_id}")
        
        format_name = TOURNAMENT_FORMATS.get(tournament.get('format', '1v1'), tournament.get('format', '1v1'))
        
        # Base message text (используем более безопасное форматирование)
        message_text = (
            f"⚔️ Турнир {format_name}\n\n"
            f"🏆 Побед для прохождения: {tournament.get('wins_needed', 1)}\n"
        )
        
        # Показываем режимы только один раз
        if tournament.get('selected_modes'):
            modes_list = ", ".join(tournament['selected_modes'])
            message_text += f"🎮 Режимы: {modes_list}\n"
        
        # Add maps info
        if tournament.get('maps'):
            try:
                maps_text = map_manager.format_selected_maps(tournament['maps'])
                # Очищаем maps_text от потенциально проблемных символов
                clean_maps_text = maps_text.replace("*", "").replace("_", "")
                message_text += f"\n🗺 Выбранные карты:\n{clean_maps_text}\n"
            except Exception as e:
                logger.error(f"Error formatting maps: {e}")
        
        message_text += "\n"
        
        # Add participants list
        if participants_list:
            participants_text = "\n".join(participants_list)
            message_text += f"👥 Участники ({len(participants_list)}):\n{participants_text}\n\n"
        else:
            message_text += "👥 Участников пока нет\n\n"
        
        # Show status message
        message_text += f"⚠️ {status_message}"
        if can_start:
            message_text += " (Создатель может запустить турнир)"
        
        # Безопасная отправка сообщения
        try:
            await query.edit_message_text(
                message_text,
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Error editing tournament message: {e}")
            # Если и без Markdown не работает, отправляем минимальную версию
            try:
                simple_text = f"Турнир {format_name}\nУчастников: {len(tournament.get('participants', []))}"
                await query.edit_message_text(
                    simple_text,
                    reply_markup=reply_markup
                )
            except Exception as final_error:
                logger.error(f"Final fallback failed: {final_error}")
                # Логируем проблему, но не падаем
                pass

    async def confirm_modes_selection(self, query, context):
        """Подтверждение выбора режимов (исправленная версия)"""
        chat_id = query.message.chat_id
        tournament = active_tournaments[chat_id]
        
        # Проверяем, что только создатель может настраивать турнир
        if query.from_user.id != tournament.get('creator'):
            await query.answer("❗ Только создатель турнира может настраивать параметры")
            return
        
        # Автоматический выбор карт
        try:
            selected_maps = map_manager.get_random_maps_for_modes(tournament.get('selected_modes', []))
            tournament['maps'] = selected_maps
        except Exception as e:
            logger.error(f"Error selecting maps: {e}")
            tournament['maps'] = {}
        
        # Создание турнира в базе данных
        try:
            tournament_id = self.db.create_tournament(
                chat_id=chat_id,
                format_type=tournament.get('format', '1v1'),
                wins_needed=tournament.get('wins_needed', 1),
                modes=tournament.get('selected_modes', []),
                maps=tournament.get('maps', {})
            )
            tournament['id'] = tournament_id
        except Exception as e:
            logger.error(f"Error creating tournament in DB: {e}")
            tournament['id'] = None
        
        keyboard = [
            [InlineKeyboardButton("✅ Участвовать", callback_data="join_tournament")],
            [InlineKeyboardButton("❌ Выйти", callback_data="leave_tournament")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        format_name = TOURNAMENT_FORMATS.get(tournament.get('format', '1v1'), tournament.get('format', '1v1'))
        modes_list = ", ".join(tournament.get('selected_modes', []))
        
        # Упрощенное сообщение без сложного форматирования
        message_text = f"🎮 Турнир {format_name} создан!\n\n"
        message_text += f"🏆 Побед для прохождения: {tournament.get('wins_needed', 1)}\n"
        message_text += f"🎮 Режимы: {modes_list}\n"
        message_text += f"🗺 Карты выбраны автоматически\n\n"
        
        min_participants = MIN_TOURNAMENT_PARTICIPANTS.get(tournament.get('format', '1v1'), 2)
        message_text += f"⚠️ Минимум участников для старта: {min_participants}\n\n"
        message_text += "Нажмите кнопку ниже, чтобы присоединиться!"
        
        tournament['message_id'] = query.message.message_id
        
        try:
            await query.edit_message_text(
                message_text,
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Error in confirm_modes_selection: {e}")
            # Простое сообщение
            simple_text = f"Турнир {format_name} создан! Присоединяйтесь!"
            await query.edit_message_text(
                simple_text,
                reply_markup=reply_markup
            )

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        if update.effective_chat.type == 'private':
            await self.send_private_welcome(update, context)
        else:
            if update.message:
                await self.send_welcome_message(update, context)
            else:
                keyboard = [
                    [
                        InlineKeyboardButton("🎮 Начать турнир", callback_data="start_tournament"),
                        InlineKeyboardButton("🗓 Запланировать", callback_data="schedule_tournament")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=MESSAGES.get("welcome", "Добро пожаловать!"),
                    reply_markup=reply_markup
                )
    
    async def send_private_welcome(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Приветственное сообщение в приватном чате"""
        # Добавляем пользователя в базу данных с клубом по умолчанию
        user_id = update.effective_user.id
        try:
            self.db.add_user(user_id, update.effective_user.username, update.effective_user.first_name)
            
            # Устанавливаем клан по умолчанию, если его нет
            user = self.db.get_user(user_id)
            if not user or not user.get('clan') or user.get('clan') == 'Не указан':
                self.db.update_user_clan(user_id, 'FairDragons')
        except Exception as e:
            logger.error(f"Error setting up user: {e}")
        
        # Кнопка для добавления в группу
        bot_username = context.bot.username
        add_to_group_url = f"https://t.me/{bot_username}?startgroup=true"
        
        keyboard = [
            [InlineKeyboardButton("➕ Добавить в группу", url=add_to_group_url)],
            [InlineKeyboardButton("👤 Мой профиль", callback_data="show_profile")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_text = f"""🤖 Добро пожаловать в Brawl Stars Tournament Bot!

👋 Привет, {update.effective_user.first_name}! Я помогаю организовывать крутые турниры по Brawl Stars прямо в групповых чатах Telegram.

1. Что я умею:              

🎮 Турниры в реальном времени
   • Поддержка форматов 1v1, 2v2, 3v3
   • Автоматический выбор карт
   • Честная турнирная сетка

🗓 Планирование турниров
   • Голосования за участие
   • Напоминания о турнирах

📊 Система профилей
   • Статистика побед и участий
   • Система опыта и рангов
   • Отслеживание кубков

🔐 Система ролей
   • Разные уровни доступа
   • Модерация турниров

2. Доступные команды:          

• /profile - Показать профиль
• /setcups [число] - Установить кубки
• /help - Подробная справка
• /stats - Общая статистика
• /ranks - Информация о рангах

3. Как начать использовать?    

1️⃣ Добавьте меня в групповой чат
2️⃣ Дайте права администратора
3️⃣ Нажмите "🎮 Начать турнир"
4️⃣ Настройте параметры
5️⃣ Наслаждайтесь игрой!

Готов к турнирам? Добавляйте меня в группу! 🚀"""
        
        await update.message.reply_text(
            welcome_text,
            reply_markup=reply_markup
        )
    
    async def send_welcome_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Отправка приветственного сообщения"""
        keyboard = [
            [
                InlineKeyboardButton("🎮 Начать турнир", callback_data="start_tournament"),
                InlineKeyboardButton("🗓 Запланировать", callback_data="schedule_tournament")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            # Сначала пробуем ответить, если сообщение существует
            if update.message and update.message.message_id:
                await update.message.reply_text(
                    MESSAGES.get("welcome", "Добро пожаловать!"),
                    reply_markup=reply_markup
                )
            else:
                # Используем send_message если нет сообщения для ответа
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=MESSAGES.get("welcome", "Добро пожаловать!"),
                    reply_markup=reply_markup
                )
        except Exception as e:
            logger.error(f"Ошибка отправки приветственного сообщения: {e}")
            # Резервный вариант без markdown
            try:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="🤖 Добро пожаловать! Нажмите кнопки ниже для начала.",
                    reply_markup=reply_markup
                )
            except Exception as fallback_error:
                logger.error(f"Резервный вариант тоже не сработал: {fallback_error}")
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий на кнопки"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        chat_id = query.message.chat_id
        user_id = query.from_user.id
        
        # Добавляем пользователя в базу данных
        try:
            self.db.add_user(user_id, query.from_user.username, query.from_user.first_name)
        except Exception as e:
            logger.error(f"Error adding user: {e}")
        
        if data == "start_tournament":
            await self.start_tournament_setup(query, context)
        elif data == "schedule_tournament":
            await self.schedule_tournament(query, context)
        elif data.startswith("tournament_format_"):
            format_type = data.replace("tournament_format_", "")
            await self.select_tournament_format(query, context, format_type)
        elif data.startswith("wins_needed_"):
            wins = int(data.replace("wins_needed_", ""))
            await self.set_wins_needed(query, context, wins)
        elif data.startswith("modes_count_"):
            count = int(data.replace("modes_count_", ""))
            await self.select_modes_count(query, context, count)
        elif data.startswith("mode_"):
            mode = data.replace("mode_", "").replace("_", " ")
            await self.toggle_game_mode(query, context, mode)
        elif data == "confirm_modes":
            await self.confirm_modes_selection(query, context)
        elif data == "join_tournament":
            await self.join_tournament(query, context)
        elif data == "leave_tournament":
            await self.leave_tournament(query, context)
        elif data == "show_profile":
            await self.show_profile_callback(query, context)
        elif data == "start_bracket":
            await self.start_tournament_bracket(query, context)
        elif data.startswith("match_winner_"):
            await self.set_match_winner(query, context, data)
        elif data == "back_to_main":
            await self.send_private_welcome_edit(query, context)
        elif data == "edit_clan":
            await self.edit_clan_prompt(query, context)
        elif data == "edit_trophies":
            await self.edit_trophies_prompt(query, context)
        elif data == "change_rank":
            await self.show_ranks_info(query, context)
        elif data == "schedule_next_tournament":
            await self.schedule_tournament(query, context)
        elif data == "top_trophies":
            await self.show_top_trophies(query, context)
        elif data == "top_experience":
            await self.show_top_experience(query, context)
        elif data == "top_wins":
            await self.show_top_wins(query, context)
        elif data == "top_participations":
            await self.show_top_participations(query, context)
        elif data == "back_to_top":
            await self.show_top_callback(query, context)
    
    async def start_tournament_setup(self, query, context):
        """Начало настройки турнира"""
        try:
            user = self.db.get_user(query.from_user.id)
            
            # Проверка прав (только админы и выше могут создавать турниры)
            if not user or user.get('role') not in ['admin', 'owner'] and query.from_user.id != OWNER_ID:
                await query.edit_message_text(
                    "❌ У вас нет прав для создания турниров!\n"
                    "Обратитесь к администратору группы."
                )
                return
        except Exception as e:
            logger.error(f"Error checking user permissions: {e}")
            if query.from_user.id != OWNER_ID:
                await query.edit_message_text("❌ Ошибка проверки прав доступа!")
                return
        
        # Переход к выбору формата турнира
        keyboard = [
            [InlineKeyboardButton("⚔️ 1v1", callback_data="tournament_format_1v1")],
            [InlineKeyboardButton("🤝 2v2", callback_data="tournament_format_2v2")],
            [InlineKeyboardButton("👥 3v3", callback_data="tournament_format_3v3")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "⚙️ Настройка турнира\n\n"
            "⚔️ Выберите формат турнира:",
            reply_markup=reply_markup
        )
    
    async def select_tournament_format(self, query, context, format_type):
        """Выбор формата турнира"""
        chat_id = query.message.chat_id
        
        # Проверяем, что только создатель может настраивать турнир
        if chat_id in active_tournaments and query.from_user.id != active_tournaments[chat_id].get('creator'):
            await query.answer("❗ Только создатель турнира может настраивать параметры")
            return
        
        # Инициализация данных турнира
        active_tournaments[chat_id] = {
            'creator': query.from_user.id,
            'format': format_type,
            'wins_needed': None,
            'modes_count': None,
            'selected_modes': [],
            'maps': {},
            'participants': [],
            'bracket': {},
            'current_round': 'registration'
        }
        
        # Инициализируем трекер участия для этого чата
        if chat_id not in user_participation_tracker:
            user_participation_tracker[chat_id] = {}
        
        # Проверяем доступные режимы для выбранного формата
        try:
            available_modes = map_manager.get_all_modes_for_format(format_type)
        except Exception as e:
            logger.error(f"Error getting modes for format {format_type}: {e}")
            available_modes = ["Режим по умолчанию"]
        
        if format_type == "1v1":
            # Для 1v1 только один режим - Одиночное столкновение
            active_tournaments[chat_id]['modes_count'] = 1
            active_tournaments[chat_id]['selected_modes'] = available_modes
            
            keyboard = [
                [InlineKeyboardButton("1 победа", callback_data="wins_needed_1")],
                [InlineKeyboardButton("2 победы", callback_data="wins_needed_2")],
                [InlineKeyboardButton("3 победы", callback_data="wins_needed_3")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"✅ Формат: {TOURNAMENT_FORMATS.get(format_type, format_type)}\n"
                f"✅ Режим: {available_modes[0] if available_modes else 'Не определен'}\n\n"
                "🏆 Сколько побед нужно для прохождения?",
                reply_markup=reply_markup
            )
        else:
            # Для 2v2 и 3v3 выбираем количество режимов
            keyboard = [
                [InlineKeyboardButton("1 режим", callback_data="modes_count_1")],
                [InlineKeyboardButton("2 режима", callback_data="modes_count_2")],
                [InlineKeyboardButton("3 режима", callback_data="modes_count_3")],
                [InlineKeyboardButton("4 режима", callback_data="modes_count_4")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"✅ Формат: {TOURNAMENT_FORMATS.get(format_type, format_type)}\n\n"
                "🎮 Сколько режимов будет в турнире?",
                reply_markup=reply_markup
            )
    
    async def set_wins_needed(self, query, context, wins):
        """Установка количества побед"""
        chat_id = query.message.chat_id
        tournament = active_tournaments.get(chat_id, {})
        
        # Проверяем, что только создатель может настраивать турнир
        if query.from_user.id != tournament.get('creator'):
            await query.answer("❗ Только создатель турнира может настраивать параметры")
            return
        
        tournament['wins_needed'] = wins
        
        if tournament.get('format') == '1v1':
            # Для 1v1 сразу переходим к подтверждению
            await self.confirm_modes_selection(query, context)
        else:
            # Для командных форматов переходим к выбору режимов
            await self.show_modes_selection(query, context)
    
    async def select_modes_count(self, query, context, count):
        """Выбор количества режимов"""
        chat_id = query.message.chat_id
        tournament = active_tournaments.get(chat_id, {})
        
        # Проверяем, что только создатель может настраивать турнир
        if query.from_user.id != tournament.get('creator'):
            await query.answer("❗ Только создатель турнира может настраивать параметры")
            return
        
        active_tournaments[chat_id]['modes_count'] = count
        
        keyboard = [
            [InlineKeyboardButton("1 победа", callback_data="wins_needed_1")],
            [InlineKeyboardButton("2 победы", callback_data="wins_needed_2")],
            [InlineKeyboardButton("3 победы", callback_data="wins_needed_3")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"✅ Режимов в турнире: {count}\n\n"
            "🏆 Сколько побед нужно для прохождения?",
            reply_markup=reply_markup
        )
    
    async def show_modes_selection(self, query, context):
        """Показ выбора игровых режимов"""
        chat_id = query.message.chat_id
        tournament = active_tournaments.get(chat_id, {})
        
        # Проверяем, что только создатель может настраивать турнир
        if query.from_user.id != tournament.get('creator'):
            await query.answer("❗ Только создатель турнира может настраивать параметры")
            return
        
        modes_needed = tournament.get('modes_count', 1)
        format_type = tournament.get('format', '1v1')
        
        # Получаем доступные режимы для данного формата
        try:
            available_modes = map_manager.get_all_modes_for_format(format_type)
        except Exception as e:
            logger.error(f"Error getting modes: {e}")
            available_modes = ["Режим по умолчанию"]
        
        keyboard = []
        for mode in available_modes:
            emoji = "✅" if mode in tournament.get('selected_modes', []) else "☐"
            callback_data = f"mode_{mode.replace(' ', '_').replace('(', '').replace(')', '')}"
            keyboard.append([InlineKeyboardButton(f"{emoji} {mode}", callback_data=callback_data)])
        
        if len(tournament.get('selected_modes', [])) == modes_needed:
            keyboard.append([InlineKeyboardButton("✅ Подтвердить выбор", callback_data="confirm_modes")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        selected_text = "\n".join([f"• {mode}" for mode in tournament.get('selected_modes', [])])
        
        await query.edit_message_text(
            f"🎮 Выберите {modes_needed} режим(ов):\n\n"
            f"Выбрано ({len(tournament.get('selected_modes', []))}/{modes_needed}):\n"
            f"{selected_text}\n\n"
            "Нажмите на режимы для выбора:",
            reply_markup=reply_markup
        )
    
    async def toggle_game_mode(self, query, context, mode):
        """Переключение выбора игрового режима"""
        chat_id = query.message.chat_id
        tournament = active_tournaments.get(chat_id, {})
        
        # Проверяем, что только создатель может настраивать турнир
        if query.from_user.id != tournament.get('creator'):
            await query.answer("❗ Только создатель турнира может настраивать параметры")
            return
        
        format_type = tournament.get('format', '1v1')
        
        # Получаем доступные режимы для данного формата
        try:
            available_modes = map_manager.get_all_modes_for_format(format_type)
        except Exception as e:
            logger.error(f"Error getting modes: {e}")
            available_modes = ["Режим по умолчанию"]
        
        # Находим правильное название режима
        actual_mode = None
        for game_mode in available_modes:
            if mode.replace("_", " ") in game_mode or game_mode.replace(" ", "_").replace("(", "").replace(")", "") == mode:
                actual_mode = game_mode
                break
        
        if actual_mode:
            selected_modes = tournament.get('selected_modes', [])
            if actual_mode in selected_modes:
                selected_modes.remove(actual_mode)
            else:
                if len(selected_modes) < tournament.get('modes_count', 1):
                    selected_modes.append(actual_mode)
            tournament['selected_modes'] = selected_modes
        
        await self.show_modes_selection(query, context)
    
    async def join_tournament(self, query, context):
        """Присоединение к турниру"""
        chat_id = query.message.chat_id
        user_id = query.from_user.id
        
        if chat_id not in active_tournaments:
            await query.answer("❌ Активный турнир не найден!")
            return
        
        tournament = active_tournaments[chat_id]
        
        if user_id in tournament.get('participants', []):
            await query.answer("ℹ️ Вы уже участвуете в турнире!")
            return
        
        tournament.setdefault('participants', []).append(user_id)
        
        # Проверяем, участвовал ли пользователь уже в этом турнире
        if chat_id not in user_participation_tracker:
            user_participation_tracker[chat_id] = {}
        
        if user_id not in user_participation_tracker[chat_id]:
            try:
                self.db.add_tournament_participation(user_id)
                user_participation_tracker[chat_id][user_id] = True
            except Exception as e:
                logger.error(f"Error adding participation: {e}")
        
        await self.update_tournament_message(query, context)
        await query.answer("✅ Вы присоединились к турниру!")
    
    async def leave_tournament(self, query, context):
        """Выход из турнира"""
        chat_id = query.message.chat_id
        user_id = query.from_user.id
        
        if chat_id not in active_tournaments:
            await query.answer("❌ Активный турнир не найден!")
            return
        
        tournament = active_tournaments[chat_id]
        
        if user_id not in tournament.get('participants', []):
            await query.answer("ℹ️ Вы не участвуете в турнире!")
            return
        
        tournament.get('participants', []).remove(user_id)
        
        await self.update_tournament_message(query, context)
        await query.answer("❌ Вы покинули турнир!")
    
    async def start_tournament_bracket(self, query, context):
        """Запуск турнирной сетки"""
        chat_id = query.message.chat_id
        tournament = active_tournaments.get(chat_id, {})
        
        # Проверяем, что только создатель может запускать турнир
        if query.from_user.id != tournament.get('creator'):
            await query.answer("❗ Только создатель турнира может запустить турнир")
            return
        
        # Проверяем условия для старта
        can_start, error_message = check_tournament_start_conditions(tournament)
        if not can_start:
            await query.answer(f"❌ {error_message}")
            return
        
        # Обновляем участников в базе данных
        try:
            if tournament.get('id'):
                self.db.update_tournament_participants(tournament['id'], tournament.get('participants', []))
        except Exception as e:
            logger.error(f"Error updating participants: {e}")
        
        # Создаем турнирную сетку
        await self.create_tournament_bracket(query, context)
    
    async def create_tournament_bracket(self, query, context):
        """Создание турнирной сетки"""
        chat_id = query.message.chat_id
        tournament = active_tournaments.get(chat_id, {})
        participants = tournament.get('participants', []).copy()
        random.shuffle(participants)  # Перемешиваем участников
        
        format_type = tournament.get('format', '1v1')
        
        if format_type == '1v1':
            matches = self.create_1v1_bracket(participants)
        else:  # 2v2 или 3v3
            matches = self.create_team_bracket(participants, format_type)
        
        tournament['bracket'] = {
            'round_1': matches,
            'current_round': 'round_1',
            'winners': []
        }
        
        # Инициализируем счёт для всех матчей
        for match in matches:
            match_id = match.get('match_id', 0)
            match_scores[match_id] = {'team1_wins': 0, 'team2_wins': 0}
        
        try:
            if tournament.get('id'):
                self.db.update_tournament_bracket(tournament['id'], tournament['bracket'])
        except Exception as e:
            logger.error(f"Error updating bracket: {e}")
        
        await self.show_current_round(query, context)
    
    def create_1v1_bracket(self, participants):
        """Создание сетки для 1v1"""
        matches = []
        for i in range(0, len(participants), 2):
            if i + 1 < len(participants):
                matches.append({
                    'team1': [participants[i]],
                    'team2': [participants[i+1]],
                    'winner': None,
                    'match_id': len(matches)
                })
        return matches
    
    def create_team_bracket(self, participants, format_type):
        """Создание сетки для командных режимов"""
        team_size = 2 if format_type == '2v2' else 3
        matches = []
        
        # Создаем команды
        teams = []
        for i in range(0, len(participants), team_size):
            team = participants[i:i+team_size]
            if len(team) == team_size:
                teams.append(team)
        
        # Создаем матчи между командами
        for i in range(0, len(teams), 2):
            if i + 1 < len(teams):
                matches.append({
                    'team1': teams[i],
                    'team2': teams[i+1],
                    'winner': None,
                    'match_id': len(matches)
                })
        
        return matches
    
    async def set_match_winner(self, query, context, callback_data):
        """Установка победителя матча с поддержкой Best of N"""
        parts = callback_data.split('_')
        if len(parts) < 4:
            await query.answer("❌ Неверные данные матча!")
            return
            
        try:
            match_id = int(parts[2])
            winning_team = parts[3]
        except (ValueError, IndexError):
            await query.answer("❌ Неверные данные матча!")
            return
        
        chat_id = query.message.chat_id
        tournament = active_tournaments.get(chat_id, {})
        bracket = tournament.get('bracket', {})
        current_round = bracket.get('current_round', 'round_1')
        matches = bracket.get(current_round, [])
        wins_needed = tournament.get('wins_needed', 1)
        
        # Находим матч и добавляем победу
        match = None
        for m in matches:
            if m.get('match_id') == match_id:
                match = m
                break
        
        if not match or match.get('winner') is not None:
            await query.answer("❌ Матч уже завершен или не найден!")
            return
        
        # Обновляем счёт
        if match_id not in match_scores:
            match_scores[match_id] = {'team1_wins': 0, 'team2_wins': 0}
        
        if winning_team == 'team1':
            match_scores[match_id]['team1_wins'] += 1
            winner_team = match.get('team1', [])
        else:
            match_scores[match_id]['team2_wins'] += 1
            winner_team = match.get('team2', [])
        
        team1_wins = match_scores[match_id]['team1_wins']
        team2_wins = match_scores[match_id]['team2_wins']
        
        # Получаем имена победителей раунда
        winner_names = await self.get_team_names(winner_team, context, chat_id)
        
        # Проверяем, достиг ли кто-то нужного количества побед
        if team1_wins >= wins_needed:
            match['winner'] = match['team1']
            final_winner_names = await self.get_team_names(match['team1'], context, chat_id)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ Победитель матча: {final_winner_names}\nИтоговый счёт: {team1_wins}:{team2_wins}"
            )
        elif team2_wins >= wins_needed:
            match['winner'] = match['team2']
            final_winner_names = await self.get_team_names(match['team2'], context, chat_id)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ Победитель матча: {final_winner_names}\nИтоговый счёт: {team1_wins}:{team2_wins}"
            )
        else:
            # Матч продолжается
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🏆 Победитель раунда: {winner_names}\nСчёт: {team1_wins}:{team2_wins}"
            )
        
        # Проверяем, завершился ли весь раунд
        all_finished = all(match.get('winner') is not None for match in matches)
        
        if all_finished:
            # Собираем победителей
            winners = [match.get('winner', []) for match in matches if match.get('winner')]
            winners_flat = [player for team in winners for player in team]
            
            # КРИТИЧЕСКАЯ ПРОВЕРКА: если остался только 1 победитель - турнир завершен
            if len(winners) == 1:
                # ТУРНИР ЗАВЕРШЕН - остался только один победитель
                tournament['bracket']['winners'] = winners_flat
                await self.finish_tournament(query, context)
            else:
                # Создаем следующий раунд
                next_round = f"round_{int(current_round.split('_')[1]) + 1}"
                if tournament.get('format') == '1v1':
                    next_matches = self.create_1v1_bracket(winners_flat)
                else:
                    next_matches = self.create_team_bracket(winners_flat, tournament.get('format', '2v2'))
                
                # Инициализируем счёт для новых матчей
                for new_match in next_matches:
                    match_scores[new_match.get('match_id', 0)] = {'team1_wins': 0, 'team2_wins': 0}
                
                bracket[next_round] = next_matches
                bracket['current_round'] = next_round
                
                try:
                    if tournament.get('id'):
                        self.db.update_tournament_bracket(tournament['id'], bracket)
                except Exception as e:
                    logger.error(f"Error updating bracket: {e}")
                    
                await self.show_current_round(query, context)
        else:
            await self.show_current_round(query, context)
        
        await query.answer("✅ Победитель установлен!")
    
    def determine_tournament_winners(self, tournament):
        """Определение призеров турнира на основе турнирной сетки"""
        bracket = tournament.get('bracket', {})
        format_type = tournament.get('format', '1v1')
        
        winners_data = []
        
        # Найдем финальный раунд
        final_round_key = None
        max_round = 0
        for round_key in bracket.keys():
            if round_key.startswith('round_'):
                try:
                    round_num = int(round_key.split('_')[1])
                    if round_num > max_round:
                        max_round = round_num
                        final_round_key = round_key
                except (ValueError, IndexError):
                    continue
        
        if not final_round_key or final_round_key not in bracket:
            return winners_data
        
        final_matches = bracket[final_round_key]
        
        # 1 место - победитель финала
        for match in final_matches:
            if match.get('winner') is not None:
                winners_data.append({
                    'place': 1,
                    'team': match['winner']
                })
                
                # 2 место - проигравший в финале
                if match['winner'] == match.get('team1', []):
                    second_place = match.get('team2', [])
                else:
                    second_place = match.get('team1', [])
                
                winners_data.append({
                    'place': 2,
                    'team': second_place
                })
                break
        
        # 3 место - проигравшие в полуфинале (если есть)
        if max_round > 1:
            semifinal_round_key = f"round_{max_round - 1}"
            if semifinal_round_key in bracket:
                semifinal_matches = bracket[semifinal_round_key]
                
                # Находим проигравших в полуфинале
                semifinal_losers = []
                for match in semifinal_matches:
                    if match.get('winner') is not None:
                        if match['winner'] == match.get('team1', []):
                            semifinal_losers.append(match.get('team2', []))
                        else:
                            semifinal_losers.append(match.get('team1', []))
                
                # Берем первого проигравшего как 3 место
                if semifinal_losers:
                    winners_data.append({
                        'place': 3,
                        'team': semifinal_losers[0]
                    })
        
        # Если нет полуфинала, 3 место может быть первым исключенным
        if len(winners_data) < 3 and max_round == 1:
            # Ищем любого участника, который не вошел в финал
            all_participants = tournament.get('participants', [])
            finalists = []
            for match in final_matches:
                finalists.extend(match.get('team1', []))
                finalists.extend(match.get('team2', []))
            
            for participant in all_participants:
                if participant not in finalists:
                    if format_type == '1v1':
                        winners_data.append({
                            'place': 3,
                            'team': [participant]
                        })
                        break
        
        return winners_data

    async def finish_tournament(self, query, context):
        """Завершение турнира с правильным определением призеров"""
        chat_id = query.message.chat_id
        tournament = active_tournaments.get(chat_id, {})
        
        # Определяем призеров на основе турнирной сетки
        winners_data = self.determine_tournament_winners(tournament)
        
        # Начисляем награды
        winners_text = ""
        for i, winner_data in enumerate(winners_data[:3]):
            place = i + 1
            team = winner_data.get('team', [])
            
            # Начисляем XP всем участникам команды/игроку
            xp_rewards = {1: 100, 2: 75, 3: 50}
            xp = xp_rewards.get(place, 0)
            
            for user_id in team:
                try:
                    self.db.add_tournament_win(user_id, place)
                except Exception as e:
                    logger.error(f"Error adding win for user {user_id}: {e}")
            
            # Получаем имена для отображения
            team_names = await self.get_team_names(team, context, chat_id)
            
            if place == 1:
                winners_text += f"🥇 1 место — {team_names} (+{xp} XP)\n"
            elif place == 2:
                winners_text += f"🥈 2 место — {team_names} (+{xp} XP)\n"
            elif place == 3:
                winners_text += f"🥉 3 место — {team_names} (+{xp} XP)\n"
        
        # Завершаем турнир в базе данных
        try:
            if tournament.get('id'):
                self.db.finish_tournament(tournament['id'])
        except Exception as e:
            logger.error(f"Error finishing tournament: {e}")
        
        # Очищаем данные матчей
        bracket = tournament.get('bracket', {})
        for round_key in bracket.keys():
            if round_key.startswith('round_'):
                for match in bracket[round_key]:
                    match_id = match.get('match_id', 0)
                    if match_id in match_scores:
                        del match_scores[match_id]
        
        # Удаляем из активных турниров и очищаем трекер участия
        if chat_id in active_tournaments:
            del active_tournaments[chat_id]
        if chat_id in user_participation_tracker:
            del user_participation_tracker[chat_id]
        
        # Кнопка для планирования следующего турнира
        keyboard = [[InlineKeyboardButton("📆 Запланировать следующий турнир", callback_data="schedule_next_tournament")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        format_name = TOURNAMENT_FORMATS.get(tournament.get('format', '1v1'), tournament.get('format', '1v1'))
        final_message = f"""🏁 Турнир {format_name} завершён!

{winners_text}

🎉 Поздравляем победителей!
🎮 Спасибо всем за участие!

XP добавлен в профили игроков"""
        
        await query.edit_message_text(
            final_message,
            reply_markup=reply_markup
        )

    async def top_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда показа топа игроков"""
        keyboard = [
            [
                InlineKeyboardButton("🏅 Топ по кубкам", callback_data="top_trophies"),
                InlineKeyboardButton("📚 Топ по опыту", callback_data="top_experience")
            ],
            [
                InlineKeyboardButton("🏆 Топ по победам", callback_data="top_wins"),
                InlineKeyboardButton("🎮 Топ по участиям", callback_data="top_participations")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "📊 Топ игроков\n\nВыберите категорию для просмотра рейтинга:",
            reply_markup=reply_markup
        )

    async def show_top_trophies(self, query, context):
        """Показ топа по кубкам"""
        try:
            if hasattr(self.db, 'get_top_users_by_trophies'):
                top_users = self.db.get_top_users_by_trophies(10)
            else:
                # Fallback если метод не существует
                top_users = []
                logger.warning("Method get_top_users_by_trophies not found in database")
        except Exception as e:
            logger.error(f"Error getting top trophies: {e}")
            top_users = []
        
        if not top_users:
            await query.edit_message_text("📊 Пока нет данных для отображения топа!")
            return
        
        top_text = "🏅 Топ по кубкам:\n\n"
        
        for i, user_data in enumerate(top_users, 1):
            username = user_data.get('username', 'Неизвестный')
            trophies = user_data.get('trophies', 0)
            
            if i == 1:
                emoji = "🥇"
            elif i == 2:
                emoji = "🥈"
            elif i == 3:
                emoji = "🥉"
            else:
                emoji = f"{i}."
            
            if username != 'Неизвестный':
                top_text += f"{emoji} @{username} — {trophies:,} 🏅\n"
            else:
                top_text += f"{emoji} {username} — {trophies:,} 🏅\n"
        
        keyboard = [
            [InlineKeyboardButton("🔙 Назад к топу", callback_data="back_to_top")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(top_text, reply_markup=reply_markup)

    async def show_top_experience(self, query, context):
        """Показ топа по опыту"""
        try:
            if hasattr(self.db, 'get_top_users_by_experience'):
                top_users = self.db.get_top_users_by_experience(10)
            else:
                # Fallback если метод не существует
                top_users = []
                logger.warning("Method get_top_users_by_experience not found in database")
        except Exception as e:
            logger.error(f"Error getting top experience: {e}")
            top_users = []
        
        if not top_users:
            await query.edit_message_text("📊 Пока нет данных для отображения топа!")
            return
        
        top_text = "📚 Топ по опыту:\n\n"
        
        for i, user_data in enumerate(top_users, 1):
            username = user_data.get('username', 'Неизвестный')
            xp = user_data.get('xp', 0)
            rank = user_data.get('rank', get_top_users_by_experience(xp))
            
            if i == 1:
                emoji = "🥇"
            elif i == 2:
                emoji = "🥈"  
            elif i == 3:
                emoji = "🥉"
            else:
                emoji = f"{i}."
            
            if username != 'Неизвестный':
                top_text += f"{emoji} @{username} — {xp} XP ({rank})\n"
            else:
                top_text += f"{emoji} {username} — {xp} XP ({rank})\n"
        
        keyboard = [
            [InlineKeyboardButton("🔙 Назад к топу", callback_data="back_to_top")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(top_text, reply_markup=reply_markup)

    async def show_top_wins(self, query, context):
        """Показ топа по победам"""
        try:
            if hasattr(self.db, 'get_top_users_by_wins'):
                top_users = self.db.get_top_users_by_wins(10)
            else:
                # Fallback если метод не существует
                top_users = []
                logger.warning("Method get_top_users_by_wins not found in database")
        except Exception as e:
            logger.error(f"Error getting top wins: {e}")
            top_users = []
        
        if not top_users:
            await query.edit_message_text("📊 Пока нет данных для отображения топа!")
            return
        
        top_text = "🏆 Топ по победам:\n\n"
        
        for i, user_data in enumerate(top_users, 1):
            username = user_data.get('username', 'Неизвестный')
            wins = user_data.get('wins', 0)
            win_rate = user_data.get('win_rate', 0)
            
            if i == 1:
                emoji = "🥇"
            elif i == 2:
                emoji = "🥈"
            elif i == 3:
                emoji = "🥉"
            else:
                emoji = f"{i}."
            
            if username != 'Неизвестный':
                top_text += f"{emoji} @{username} — {wins} побед ({win_rate}%)\n"
            else:
                top_text += f"{emoji} {username} — {wins} побед ({win_rate}%)\n"
        
        keyboard = [
            [InlineKeyboardButton("🔙 Назад к топу", callback_data="back_to_top")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(top_text, reply_markup=reply_markup)

    async def show_top_participations(self, query, context):
        """Показ топа по участиям"""
        try:
            if hasattr(self.db, 'get_top_users_by_participations'):
                top_users = self.db.get_top_users_by_participations(10)
            else:
                # Fallback если метод не существует
                top_users = []
                logger.warning("Method get_top_users_by_participations not found in database")
        except Exception as e:
            logger.error(f"Error getting top participations: {e}")
            top_users = []
        
        if not top_users:
            await query.edit_message_text("📊 Пока нет данных для отображения топа!")
            return
        
        top_text = "🎮 Топ по участиям:\n\n"
        
        for i, user_data in enumerate(top_users, 1):
            username = user_data.get('username', 'Неизвестный')
            participations = user_data.get('participations', 0)
            wins = user_data.get('wins', 0)
            
            if i == 1:
                emoji = "🥇"
            elif i == 2:
                emoji = "🥈"
            elif i == 3:
                emoji = "🥉"
            else:
                emoji = f"{i}."
            
            if username != 'Неизвестный':
                top_text += f"{emoji} @{username} — {participations} участий ({wins} побед)\n"
            else:
                top_text += f"{emoji} {username} — {participations} участий ({wins} побед)\n"
        
        keyboard = [
            [InlineKeyboardButton("🔙 Назад к топу", callback_data="back_to_top")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(top_text, reply_markup=reply_markup)

    async def show_top_callback(self, query, context):
        """Показ топа через callback"""
        keyboard = [
            [
                InlineKeyboardButton("🏅 Топ по кубкам", callback_data="top_trophies"),
                InlineKeyboardButton("📚 Топ по опыту", callback_data="top_experience")
            ],
            [
                InlineKeyboardButton("🏆 Топ по победам", callback_data="top_wins"),
                InlineKeyboardButton("🎮 Топ по участиям", callback_data="top_participations")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "📊 Топ игроков\n\nВыберите категорию для просмотра рейтинга:",
            reply_markup=reply_markup
        )

    async def schedule_tournament(self, query, context):
        """Планирование турнира"""
        chat_id = query.message.chat_id
        
        # Создаем голосование
        tomorrow = datetime.now() + timedelta(hours=SCHEDULED_TOURNAMENT_HOURS)
        poll_question = f"🕘 Сможете ли вы участвовать сегодня в 22:00 (по Киеву)?"
        
        try:
            poll = await context.bot.send_poll(
                chat_id=chat_id,
                question=poll_question,
                options=["✅ Да", "❌ Нет"],
                is_anonymous=False,
                allows_multiple_answers=False
            )
            
            # Сохраняем информацию о голосовании
            schedule_id = self.db.add_scheduled_tournament(
                chat_id=chat_id,
                poll_message_id=poll.message_id,
                scheduled_time=tomorrow.isoformat()
            )
            
            active_polls[poll.poll.id] = {
                'schedule_id': schedule_id,
                'chat_id': chat_id,
                'participants': []
            }
            
            await query.edit_message_text("📊 Голосование создано!")
        except Exception as e:
            logger.error(f"Error creating poll: {e}")
            await query.edit_message_text("❌ Ошибка создания голосования!")
    
    async def poll_answer(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик ответов на голосование"""
        poll_answer = update.poll_answer
        poll_id = poll_answer.poll_id
        user_id = poll_answer.user.id
        
        if poll_id in active_polls:
            poll_data = active_polls[poll_id]
            
            # Если пользователь выбрал "Да" (option_ids[0] == 0)
            if 0 in poll_answer.option_ids:
                if user_id not in poll_data['participants']:
                    poll_data['participants'].append(user_id)
                    try:
                        self.db.update_scheduled_participants(
                            poll_data['schedule_id'], 
                            poll_data['participants']
                        )
                    except Exception as e:
                        logger.error(f"Error updating scheduled participants: {e}")
            else:
                # Если пользователь выбрал "Нет" или изменил ответ
                if user_id in poll_data['participants']:
                    poll_data['participants'].remove(user_id)
                    try:
                        self.db.update_scheduled_participants(
                            poll_data['schedule_id'], 
                            poll_data['participants']
                        )
                    except Exception as e:
                        logger.error(f"Error updating scheduled participants: {e}")
    
    async def profile_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Улучшенная команда показа профиля"""
        user_id = update.effective_user.id
        username = update.effective_user.username or update.effective_user.first_name
        
        # Добавляем пользователя в базу данных с клубом по умолчанию
        try:
            self.db.add_user(user_id, update.effective_user.username, update.effective_user.first_name)
            
            # Устанавливаем клан по умолчанию, если его нет
            user = self.db.get_user(user_id)
            if not user or not user.get('clan') or user.get('clan') == 'Не указан':
                self.db.update_user_clan(user_id, 'FairDragons')
        except Exception as e:
            logger.error(f"Error setting up user profile: {e}")
        
        try:
            stats = self.db.get_user_stats(user_id) if hasattr(self.db, 'get_user_stats') else {}
            user = self.db.get_user(user_id) if hasattr(self.db, 'get_user') else {}
        except Exception as e:
            logger.error(f"Error getting user data: {e}")
            stats = {}
            user = {}
        
        if not user:
            await update.message.reply_text("❌ Профиль не найден!")
            return
        
        # Определяем роль на русском с поддержкой Owner
        role_names = {
            'user': 'Пользователь',
            'moderator': 'Модератор',
            'admin': 'Администратор',
            'owner': 'Владелец'
        }
        
        # Особая проверка для Owner по ID
        if user_id == OWNER_ID:
            role_display = 'Владелец'
        else:
            role_display = role_names.get(user.get('role', 'user'), 'Пользователь')
        
        # Безопасное получение данных с fallback значениями
        trophies = stats.get('trophies', 0)
        clan = stats.get('clan', user.get('clan', 'FairDragons'))
        xp = stats.get('xp', 0)
        rank = stats.get('rank', get_rank_by_xp(xp))
        wins = stats.get('wins', 0)
        participations = stats.get('participations', 0)
        win_rate = stats.get('win_rate', 0 if participations == 0 else round((wins / participations) * 100, 1))
        
        profile_text = f"""👤 Профиль игрока

🧑 Никнейм: @{username if update.effective_user.username else username}
🏆 Кубки: {trophies:,} 🏅
🔰 Роль: {role_display}
🏴 Клан: {clan}
📚 Опыт: {xp} XP
🎖️ Ранг: {rank}

🥇 Побед: {wins}
🎮 Участий: {participations}
🎯 Процент побед: {win_rate}%

Используйте команды:
• /setcups [число] - изменить кубки
• /setclan [название] - изменить клан"""
        
        # Кнопки для приватного чата
        if update.effective_chat.type == 'private':
            keyboard = [
                [
                    InlineKeyboardButton("✏️ Изменить клан", callback_data="edit_clan"),
                    InlineKeyboardButton("✏️ Изменить кубки", callback_data="edit_trophies")
                ],
                [InlineKeyboardButton("ℹ️ Инфо о ранге", callback_data="change_rank")],
                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(profile_text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(profile_text)
    
    async def setcups_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда установки кубков"""
        if not context.args:
            await update.message.reply_text("❌ Укажите количество кубков!\nПример: /setcups 25000")
            return
        
        try:
            trophies = int(context.args[0])
            if trophies < 0:
                await update.message.reply_text("❌ Количество кубков не может быть отрицательным!")
                return
            
            user_id = update.effective_user.id
            
            try:
                self.db.add_user(user_id, update.effective_user.username, update.effective_user.first_name)
                
                # Устанавливаем клан по умолчанию, если его нет
                user = self.db.get_user(user_id)
                if not user or not user.get('clan') or user.get('clan') == 'Не указан':
                    self.db.update_user_clan(user_id, 'FairDragons')
                
                if self.db.update_user_trophies(user_id, trophies):
                    await update.message.reply_text(f"✅ Кубки обновлены: {trophies:,} 🏅")
                else:
                    await update.message.reply_text("❌ Ошибка при обновлении кубков!")
            except Exception as e:
                logger.error(f"Error updating trophies: {e}")
                await update.message.reply_text("❌ Ошибка при обновлении кубков!")
        
        except ValueError:
            await update.message.reply_text("❌ Неверный формат числа!")
    
    async def setclan_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда установки клана"""
        if not context.args:
            await update.message.reply_text("❌ Укажите название клана!\nПример: /setclan BRAWL STARS")
            return
        
        clan_name = " ".join(context.args)
        
        if len(clan_name) > 50:
            await update.message.reply_text("❌ Название клана слишком длинное! Максимум 50 символов.")
            return
        
        user_id = update.effective_user.id
        
        try:
            self.db.add_user(user_id, update.effective_user.username, update.effective_user.first_name)
            
            if self.db.update_user_clan(user_id, clan_name):
                await update.message.reply_text(f"✅ Клан обновлен: {clan_name} 👑")
            else:
                await update.message.reply_text("❌ Ошибка при обновлении клана!")
        except Exception as e:
            logger.error(f"Error updating clan: {e}")
            await update.message.reply_text("❌ Ошибка при обновлении клана!")
    
    async def ranks_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда информации о рангах"""
        ranks_text = """🎖️ Система рангов Brawl Stars Tournament Bot

Ранги отражают ваш опыт и мастерство в турнирах. Они зависят от количества XP (опыта), которое вы получаете за участие и победы.

📊 Таблица рангов:

🙋 Новичок — 0-49 XP
• Только начинаете свой путь в турнирах
• Изучаете механики и стратегии игры
• Получаете базовые навыки

🥉 Любитель — 50-199 XP  
• Регулярно участвуете в турнирах
• Понимаете основы различных режимов
• Начинаете побеждать

🎖️ Эксперт — 200-499 XP
• Опытный игрок с хорошей статистикой
• Знаете тактики всех режимов игры
• Умеете работать в команде

🏆 Мастер — 500-999 XP
• Профессиональный уровень игры
• Можете обучать новых игроков
• Стабильно показываете высокие результаты

👑 Легенда — 1000+ XP
• Элитный игрок сообщества
• Один из лучших турнирных бойцов
• Пример для подражания

💰 Как получить XP:
• 🥇 1 место: +100 XP + повышение статистики
• 🥈 2 место: +75 XP
• 🥉 3 место: +50 XP
• 🎮 Участие: засчитывается в общую статистику

🎯 Преимущества высоких рангов:
• Престиж в сообществе
• Уважение других игроков
• Мотивация для дальнейшего развития
• Возможность стать ментором для новичков

Участвуйте в турнирах, побеждайте и повышайте свой ранг! Каждая игра делает вас сильнее! 💪"""
        
        await update.message.reply_text(ranks_text)
    
    async def giverole_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """ИСПРАВЛЕННАЯ команда выдачи ролей (только для владельца с ID из OWNER_ID)"""
        # Проверяем, что команду выполняет именно владелец
        if update.effective_user.id != OWNER_ID:
            await update.message.reply_text("❌ Только владелец может выдавать роли!")
            return
        
        if len(context.args) < 2:
            await update.message.reply_text(
                "❌ Неверный формат!\n"
                "Использование: /giverole @username роль\n"
                "или: /giverole 123456789 роль\n\n"
                "Доступные роли: admin, moderator, user"
            )
            return
        
        # Получаем пользователя и роль
        user_identifier = context.args[0]
        role = context.args[1].lower()
        
        # Проверяем корректность роли
        if role not in ['admin', 'moderator', 'user']:
            await update.message.reply_text("❌ Неверная роль! Доступны: admin, moderator, user")
            return
        
        target_user_id = None
        target_username = None
        
        # Определяем, это username или user_id
        if user_identifier.startswith('@'):
            # Это username
            target_username = user_identifier[1:]  # убираем @
            
            # Проверяем, есть ли метод get_user_by_username
            try:
                if hasattr(self.db, 'get_user_by_username'):
                    target_user = self.db.get_user_by_username(target_username)
                    if target_user:
                        target_user_id = target_user['user_id']
                    else:
                        await update.message.reply_text(
                            f"❌ Пользователь @{target_username} не найден в базе данных!\n"
                            "Пользователь должен сначала написать /start боту."
                        )
                        return
                else:
                    await update.message.reply_text(
                        "❌ Поиск по username временно недоступен!\n"
                        "Используйте ID пользователя вместо @username"
                    )
                    return
            except Exception as e:
                logger.error(f"Error finding user by username: {e}")
                await update.message.reply_text("❌ Ошибка поиска пользователя!")
                return
        else:
            # Это должен быть user_id
            try:
                target_user_id = int(user_identifier)
                target_user = self.db.get_user(target_user_id)
                if not target_user:
                    await update.message.reply_text(
                        f"❌ Пользователь с ID {target_user_id} не найден в базе данных!\n"
                        "Пользователь должен сначала написать /start боту."
                    )
                    return
                target_username = target_user.get('username', str(target_user_id))
            except ValueError:
                await update.message.reply_text("❌ Неверный формат ID пользователя!")
                return
            except Exception as e:
                logger.error(f"Error getting user: {e}")
                await update.message.reply_text("❌ Ошибка получения данных пользователя!")
                return
        
        # ВАЖНО: Проверяем, что не пытаемся изменить роль владельца
        if target_user_id == OWNER_ID:
            await update.message.reply_text("❌ Нельзя изменить роль владельца!")
            return
        
        # Обновляем роль пользователя
        try:
            if self.db.update_user_role(target_user_id, role):
                role_names = {
                    'user': 'Пользователь',
                    'moderator': 'Модератор', 
                    'admin': 'Администратор'
                }
                role_display = role_names.get(role, role)
                
                if target_username.isdigit():
                    await update.message.reply_text(
                        f"✅ Роль \"{role_display}\" выдана пользователю ID {target_username}"
                    )
                else:
                    await update.message.reply_text(
                        f"✅ Роль \"{role_display}\" выдана пользователю @{target_username}"
                    )
            else:
                await update.message.reply_text("❌ Ошибка при обновлении роли!")
        except Exception as e:
            logger.error(f"Error updating user role: {e}")
            await update.message.reply_text("❌ Ошибка при обновлении роли!")
    
    async def help_win_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда помощи по фиксации победителя для модераторов и админов"""
        user_id = update.effective_user.id
        
        try:
            user = self.db.get_user(user_id)
            
            # Проверка прав (только модераторы и выше)
            if not user or user.get('role') not in ['moderator', 'admin', 'owner'] and user_id != OWNER_ID:
                await update.message.reply_text("❌ Эта команда доступна только модераторам и администраторам!")
                return
        except Exception as e:
            logger.error(f"Error checking user permissions: {e}")
            if user_id != OWNER_ID:
                await update.message.reply_text("❌ Ошибка проверки прав доступа!")
                return
        
        help_text = """👨‍⚖️ Помощь по фиксации победителя:

1. Следи за матчем и определяй победителя вручную

2. Используй кнопку "🏆 Команда X" рядом с матчем или команду:
   /winner [номер_матча] [1 или 2]

3. После нужного количества побед бот перейдёт к следующему раунду

📝 Примеры команд:
• /winner 1 1 - победила команда 1 в матче №1
• /winner 2 2 - победила команда 2 в матче №2

📊 Дополнительные команды:
• /matches - показать активные матчи
• /bracket - показать турнирную сетку

⚠️ Важно: В турнирах до 2-3 побед матч не завершается после первой победы, а продолжается до достижения нужного количества побед!"""
        
        await update.message.reply_text(help_text)
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда помощи"""
        help_text = """🤖 Brawl Stars Tournament Bot

Основные команды:
• /start - Начать работу с ботом
• /profile - Показать профиль игрока
• /setcups [число] - Установить количество кубков
• /setclan [название] - Установить клан
• /top - Показать топ игроков по разным категориям
• /ranks - Информация о системе рангов
• /help - Показать справку
• /stats - Общая статистика

Для модераторов и администраторов:
• /winner [матч] [1|2] - Установить победителя
• /matches - Показать активные матчи
• /bracket - Показать турнирную сетку
• /help_win - Помощь по определению победителей

Для администраторов:
• /giverole @user роль - Выдать роль (только владелец)

Система ролей:
• 👑 Owner - полный контроль над ботом
• 🛡 Admin - создание и управление турнирами  
• 🧹 Moderator - модерация боев и определение победителей
• 🙋 User - участие в турнирах

Как организовать турнир:
1. Добавьте бота в групповой чат
2. Дайте боту права администратора  
3. Нажмите "🎮 Начать турнир"
4. Настройте параметры турнира (только создатель)
5. Дождитесь участников (минимум зависит от формата)
6. Запустите турнирную сетку (только создатель)
7. Модераторы определяют победителей матчей

Особенности:
• Автоматический выбор карт для каждого режима
• Честная турнирная сетка с перемешиванием
• Система опыта и рангов
• Поддержка матчей до 2-3 побед (Best of N)
• Подробная статистика игроков

Бот создан для честных и увлекательных турниров! Удачи в боях! ⚔️"""
        
        await update.message.reply_text(help_text)
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда общей статистики"""
        # Получаем базовую статистику из базы данных
        try:
            total_users = self.db.get_total_users() if hasattr(self.db, 'get_total_users') else 0
            total_tournaments = self.db.get_total_tournaments() if hasattr(self.db, 'get_total_tournaments') else 0
            total_finished = self.db.get_finished_tournaments() if hasattr(self.db, 'get_finished_tournaments') else 0
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            total_users = 0
            total_tournaments = 0
            total_finished = 0
        
        stats_text = f"""📊 Общая статистика бота

👥 Зарегистрированных пользователей: {total_users}
🎮 Активных турниров: {len(active_tournaments)}
🏆 Всего создано турниров: {total_tournaments}
✅ Завершено турниров: {total_finished}
📊 Активных голосований: {len(active_polls)}

Минимум участников для турнира:
• 1v1: {MIN_TOURNAMENT_PARTICIPANTS.get('1v1', 2)} участников
• 2v2: {MIN_TOURNAMENT_PARTICIPANTS.get('2v2', 4)} участников  
• 3v3: {MIN_TOURNAMENT_PARTICIPANTS.get('3v3', 6)} участников

Статистика обновляется в реальном времени!"""
        
        await update.message.reply_text(stats_text)
    
    async def show_profile_callback(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Показ профиля через callback"""
        user_id = query.from_user.id
        username = query.from_user.username or query.from_user.first_name
        
        try:
            self.db.add_user(user_id, query.from_user.username, query.from_user.first_name)
            
            # Устанавливаем клан по умолчанию, если его нет
            user = self.db.get_user(user_id)
            if not user or not user.get('clan') or user.get('clan') == 'Не указан':
                self.db.update_user_clan(user_id, 'FairDragons')
        except Exception as e:
            logger.error(f"Error setting up user: {e}")
        
        try:
            stats = self.db.get_user_stats(user_id) if hasattr(self.db, 'get_user_stats') else {}
            user = self.db.get_user(user_id) if hasattr(self.db, 'get_user') else {}
        except Exception as e:
            logger.error(f"Error getting user data: {e}")
            stats = {}
            user = {}
        
        if not user:
            await query.edit_message_text("❌ Профиль не найден!")
            return
        
        # Определяем роль на русском с поддержкой Owner
        role_names = {
            'user': 'Пользователь',
            'moderator': 'Модератор',
            'admin': 'Администратор',
            'owner': 'Владелец'
        }
        
        # Особая проверка для Owner по ID
        if user_id == OWNER_ID:
            role_display = 'Владелец'
        else:
            role_display = role_names.get(user.get('role', 'user'), 'Пользователь')
        
        # Безопасное получение данных с fallback значениями
        trophies = stats.get('trophies', 0)
        clan = stats.get('clan', user.get('clan', 'FairDragons'))
        xp = stats.get('xp', 0)
        rank = stats.get('rank', get_rank_by_xp(xp))
        wins = stats.get('wins', 0)
        participations = stats.get('participations', 0)
        win_rate = stats.get('win_rate', 0 if participations == 0 else round((wins / participations) * 100, 1))
        
        profile_text = f"""👤 Профиль игрока

🧑 Никнейм: @{username if query.from_user.username else username}
🏆 Кубки: {trophies:,} 🏅
🔰 Роль: {role_display}
🏴 Клан: {clan}
📚 Опыт: {xp} XP
🎖️ Ранг: {rank}

🥇 Побед: {wins}
🎮 Участий: {participations}
🎯 Процент побед: {win_rate}%"""
        
        # Кнопки управления профилем
        keyboard = [
            [
                InlineKeyboardButton("✏️ Изменить клан", callback_data="edit_clan"),
                InlineKeyboardButton("✏️ Изменить кубки", callback_data="edit_trophies")
            ],
            [InlineKeyboardButton("ℹ️ Инфо о ранге", callback_data="change_rank")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(profile_text, reply_markup=reply_markup)
    
    async def edit_clan_prompt(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Подсказка для изменения клана"""
        await query.edit_message_text(
            "👑 Изменение клана\n\n"
            "Чтобы изменить клан, используйте команду:\n"
            "/setclan [название_клана]\n\n"
            "Пример: /setclan BRAWL STARS\n\n"
            "🏴 Текущий клан по умолчанию: FairDragons\n\n"
            "Нажмите на команду, чтобы скопировать её",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад к профилю", callback_data="show_profile")]
            ])
        )
    
    async def edit_trophies_prompt(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Подсказка для изменения кубков"""
        await query.edit_message_text(
            "🏅 Изменение кубков\n\n"
            "Чтобы изменить количество кубков, используйте команду:\n"
            "/setcups [количество]\n\n"
            "Пример: /setcups 25000\n\n"
            "Нажмите на команду, чтобы скопировать её",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад к профилю", callback_data="show_profile")]
            ])
        )
    
    async def show_ranks_info(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Показ информации о рангах"""
        ranks_text = """🎖️ Система рангов

Ранги в боте зависят от количества опыта (XP), которое вы получаете за участие и победы в турнирах.

Таблица рангов:

🙋 Новичок — 0-49 XP
• Только начинаете свой путь
• Изучаете игру и стратегии

🥉 Любитель — 50-199 XP  
• Участвуете в турнирах регулярно
• Понимаете основы игры

🎖️ Эксперт — 200-499 XP
• Опытный игрок
• Знаете тактики разных режимов

🏆 Мастер — 500-999 XP
• Профессиональный уровень игры
• Можете обучать новичков

👑 Легенда — 1000+ XP
• Элитный игрок
• Один из лучших в сообществе

Как получить XP:
• 🥇 1 место: +100 XP
• 🥈 2 место: +75 XP  
• 🥉 3 место: +50 XP

Участвуйте в турнирах и повышайте свой ранг!"""
        
        await query.edit_message_text(
            ranks_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад к профилю", callback_data="show_profile")]
            ])
        )
    
    async def send_private_welcome_edit(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Возврат к главному меню в приватном чате"""
        # Кнопка для добавления в группу
        bot_username = context.bot.username
        add_to_group_url = f"https://t.me/{bot_username}?startgroup=true"
        
        keyboard = [
            [InlineKeyboardButton("➕ Добавить в группу", url=add_to_group_url)],
            [InlineKeyboardButton("👤 Мой профиль", callback_data="show_profile")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_text = f"""🤖 Добро пожаловать в Brawl Stars Tournament Bot!

👋 Привет, {query.from_user.first_name}! Я помогаю организовывать крутые турниры по Brawl Stars прямо в групповых чатах Telegram.

1. Что я умею:              

🎮 Турниры в реальном времени
   • Поддержка форматов 1v1, 2v2, 3v3
   • Автоматический выбор карт
   • Честная турнирная сетка

🗓 Планирование турниров
   • Голосования за участие
   • Напоминания о турнирах

📊 Система профилей
   • Статистика побед и участий
   • Система опыта и рангов
   • Отслеживание кубков

🔐 Система ролей
   • Разные уровни доступа
   • Модерация турниров

2. Доступные команды:          

• /profile - Показать профиль
• /setcups [число] - Установить кубки
• /help - Подробная справка
• /stats - Общая статистика
• /ranks - Информация о рангах

3. Как начать использовать?    

1️⃣ Добавьте меня в групповой чат
2️⃣ Дайте права администратора
3️⃣ Нажмите "🎮 Начать турнир"
4️⃣ Настройте параметры
5️⃣ Наслаждайтесь игрой!

Готов к турнирам? Добавляйте меня в группу! 🚀"""
        
        await query.edit_message_text(
            welcome_text,
            reply_markup=reply_markup
        )
    
    async def winner_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Улучшенная команда установки победителя матча для модераторов"""
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        
        # Проверка прав
        try:
            user = self.db.get_user(user_id)
            if not user or user.get('role') not in ['moderator', 'admin', 'owner'] and user_id != OWNER_ID:
                await update.message.reply_text("❌ У вас нет прав для установки победителей!")
                return
        except Exception as e:
            logger.error(f"Error checking permissions: {e}")
            if user_id != OWNER_ID:
                await update.message.reply_text("❌ Ошибка проверки прав доступа!")
                return
        
        # Проверка активного турнира
        if chat_id not in active_tournaments:
            await update.message.reply_text("❌ В этом чате нет активного турнира!")
            return
        
        tournament = active_tournaments[chat_id]
        bracket = tournament.get('bracket', {})
        current_round = bracket.get('current_round', 'round_1')
        
        if current_round == 'registration':
            await update.message.reply_text("❌ Турнир еще не начался!")
            return
        
        if not context.args or len(context.args) < 2:
            await update.message.reply_text(
                "❌ Неверный формат команды!\n\n"
                "Использование: /winner [номер_матча] [1 или 2]\n\n"
                "Примеры:\n"
                "• /winner 1 1 - победила команда 1 в матче №1\n"
                "• /winner 2 2 - победила команда 2 в матче №2\n\n"
                "Посмотрите активные матчи: /matches"
            )
            return
        
        try:
            match_number = int(context.args[0]) - 1  # Пользователи вводят 1-based, мы используем 0-based
            team_number = int(context.args[1])
            
            if team_number not in [1, 2]:
                await update.message.reply_text("❌ Номер команды должен быть 1 или 2!")
                return
            
            matches = bracket.get(current_round, [])
            
            if match_number < 0 or match_number >= len(matches):
                await update.message.reply_text(f"❌ Неверный номер матча! Доступны матчи: 1-{len(matches)}")
                return
            
            match = matches[match_number]
            
            if match.get('winner') is not None:
                await update.message.reply_text("❌ Победитель этого матча уже определен!")
                return
            
            match_id = match.get('match_id', 0)
            wins_needed = tournament.get('wins_needed', 1)
            
            # Обновляем счёт
            if match_id not in match_scores:
                match_scores[match_id] = {'team1_wins': 0, 'team2_wins': 0}
            
            if team_number == 1:
                match_scores[match_id]['team1_wins'] += 1
                winner_team = match.get('team1', [])
            else:
                match_scores[match_id]['team2_wins'] += 1
                winner_team = match.get('team2', [])
            
            team1_wins = match_scores[match_id]['team1_wins']
            team2_wins = match_scores[match_id]['team2_wins']
            
            # Получаем имена победителей раунда
            winner_names = await self.get_team_names(winner_team, context, chat_id)
            
            # Проверяем, достиг ли кто-то нужного количества побед
            if team1_wins >= wins_needed:
                match['winner'] = match['team1']
                final_winner_names = await self.get_team_names(match['team1'], context, chat_id)
                await update.message.reply_text(
                    f"✅ Победитель матча: {final_winner_names}\n"
                    f"Итоговый счёт: {team1_wins}:{team2_wins}\n\n"
                    f"🎖️ Модератор: @{update.effective_user.username or update.effective_user.first_name}"
                )
            elif team2_wins >= wins_needed:
                match['winner'] = match['team2']
                final_winner_names = await self.get_team_names(match['team2'], context, chat_id)
                await update.message.reply_text(
                    f"✅ Победитель матча: {final_winner_names}\n"
                    f"Итоговый счёт: {team1_wins}:{team2_wins}\n\n"
                    f"🎖️ Модератор: @{update.effective_user.username or update.effective_user.first_name}"
                )
            else:
                # Матч продолжается
                await update.message.reply_text(
                    f"🏆 Победитель раунда: {winner_names}\n"
                    f"Счёт: {team1_wins}:{team2_wins}\n\n"
                    f"🎖️ Модератор: @{update.effective_user.username or update.effective_user.first_name}"
                )
            
            # Обновляем в базе данных
            try:
                if tournament.get('id'):
                    self.db.update_tournament_bracket(tournament['id'], bracket)
            except Exception as e:
                logger.error(f"Error updating bracket in DB: {e}")
            
            # Проверяем, завершился ли раунд
            all_finished = all(m.get('winner') is not None for m in matches)
            
            if all_finished:
                # Собираем победителей
                winners = [match.get('winner', []) for match in matches if match.get('winner')]
                winners_flat = [player for team in winners for player in team]
                
                if len(winners) == 1:
                    # Турнир завершен
                    tournament['bracket']['winners'] = winners_flat
                    await self.finish_tournament_by_command(update, context)
                else:
                    # Создаем следующий раунд
                    await self.create_next_round(update, context, winners_flat)
        
        except ValueError:
            await update.message.reply_text("❌ Номера матча и команды должны быть числами!")
        except Exception as e:
            logger.error(f"Error in winner command: {e}")
            await update.message.reply_text("❌ Произошла ошибка при обработке команды!")
    
    async def matches_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда показа активных матчей для модераторов"""
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        
        # Проверка прав
        try:
            user = self.db.get_user(user_id)
            if not user or user.get('role') not in ['moderator', 'admin', 'owner'] and user_id != OWNER_ID:
                await update.message.reply_text("❌ У вас нет прав для просмотра матчей!")
                return
        except Exception as e:
            logger.error(f"Error checking permissions: {e}")
            if user_id != OWNER_ID:
                await update.message.reply_text("❌ Ошибка проверки прав доступа!")
                return
        
        # Проверка активного турнира
        if chat_id not in active_tournaments:
            await update.message.reply_text("❌ В этом чате нет активного турнира!")
            return
        
        tournament = active_tournaments[chat_id]
        bracket = tournament.get('bracket', {})
        current_round = bracket.get('current_round', 'round_1')
        
        if current_round == 'registration':
            await update.message.reply_text("❌ Турнир еще не начался!")
            return
        
        matches = bracket.get(current_round, [])
        wins_needed = tournament.get('wins_needed', 1)
        
        # Определяем название раунда
        total_matches = len(matches)
        if total_matches == 1:
            round_name = "🏆 ФИНАЛ"
        elif total_matches == 2:
            round_name = "🎯 ПОЛУФИНАЛ"
        else:
            round_name = f"⚔️ Раунд {current_round.split('_')[1]}"
        
        matches_text = f"📊 {round_name}\n"
        matches_text += f"🎮 Формат: {TOURNAMENT_FORMATS.get(tournament.get('format', '1v1'), tournament.get('format', '1v1'))}\n"
        matches_text += f"🏆 До {wins_needed} побед\n\n"
        
        active_matches = []
        finished_matches = []
        
        for i, match in enumerate(matches, 1):
            team1_names = await self.get_team_names(match.get('team1', []), context, chat_id)
            team2_names = await self.get_team_names(match.get('team2', []), context, chat_id)
            
            # Получаем счёт матча
            match_id = match.get('match_id', 0)
            score = match_scores.get(match_id, {'team1_wins': 0, 'team2_wins': 0})
            score_text = f"({score['team1_wins']}:{score['team2_wins']})"
            
            if match.get('winner') is None:
                match_text = f"Матч #{i} ⏳ {score_text}\n"
                match_text += f"🟥 Команда 1: {team1_names}\n"
                match_text += f"🟦 Команда 2: {team2_names}\n"
                match_text += f"💬 /winner {i} 1 или /winner {i} 2"
                active_matches.append(match_text)
            else:
                winner_names = await self.get_team_names(match.get('winner', []), context, chat_id)
                match_text = f"Матч #{i} ✅ {score_text}\n"
                match_text += f"🏆 Победитель: {winner_names}"
                finished_matches.append(match_text)
        
        if active_matches:
            matches_text += "⏳ Активные матчи:\n\n"
            matches_text += "\n\n".join(active_matches)
        
        if finished_matches:
            matches_text += "\n\n" if active_matches else ""
            matches_text += "✅ Завершенные матчи:\n\n"
            matches_text += "\n\n".join(finished_matches)
        
        if not active_matches and not finished_matches:
            matches_text += "❌ Нет активных матчей."
        
        await update.message.reply_text(matches_text)
    
    async def bracket_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда показа турнирной сетки"""
        chat_id = update.effective_chat.id
        
        if chat_id not in active_tournaments:
            await update.message.reply_text("❌ В этом чате нет активного турнира!")
            return
        
        tournament = active_tournaments[chat_id]
        bracket = tournament.get('bracket', {})
        current_round = bracket.get('current_round', 'round_1')
        
        if current_round == 'registration':
            await update.message.reply_text("❌ Турнир еще не начался!")
            return
        
        format_name = TOURNAMENT_FORMATS.get(tournament.get('format', '1v1'), tournament.get('format', '1v1'))
        wins_needed = tournament.get('wins_needed', 1)
        bracket_text = f"🏆 Турнирная сетка {format_name}\n"
        bracket_text += f"🎯 До {wins_needed} побед\n\n"
        
        # Показываем все раунды
        for round_key in sorted(bracket.keys()):
            if round_key.startswith('round_'):
                try:
                    round_num = int(round_key.split('_')[1])
                    round_matches = bracket[round_key]
                    
                    if len(round_matches) == 1:
                        round_title = "🏆 ФИНАЛ"
                    elif len(round_matches) == 2:
                        round_title = "🎯 ПОЛУФИНАЛ"
                    else:
                        round_title = f"⚔️ Раунд {round_num}"
                    
                    bracket_text += f"{round_title}:\n"
                    
                    for i, match in enumerate(round_matches, 1):
                        team1_names = await self.get_team_names(match.get('team1', []), context, chat_id)
                        team2_names = await self.get_team_names(match.get('team2', []), context, chat_id)
                        
                        # Получаем счёт матча
                        match_id = match.get('match_id', 0)
                        score = match_scores.get(match_id, {'team1_wins': 0, 'team2_wins': 0})
                        score_text = f"({score['team1_wins']}:{score['team2_wins']})"
                        
                        if match.get('winner') is None:
                            if round_key == current_round:
                                bracket_text += f"🔴 {team1_names} 🆚 {team2_names} {score_text} ⏳\n"
                            else:
                                bracket_text += f"⚫ {team1_names} 🆚 {team2_names}\n"
                        else:
                            winner_names = await self.get_team_names(match.get('winner', []), context, chat_id)
                            bracket_text += f"✅ {winner_names} {score_text} 🏆\n"
                    
                    bracket_text += "\n"
                except (ValueError, IndexError) as e:
                    logger.error(f"Error processing round {round_key}: {e}")
                    continue
        
        await update.message.reply_text(bracket_text)
    
    async def create_next_round(self, update: Update, context: ContextTypes.DEFAULT_TYPE, winners_flat: List[int]):
        """Создание следующего раунда"""
        chat_id = update.effective_chat.id
        tournament = active_tournaments.get(chat_id, {})
        bracket = tournament.get('bracket', {})
        current_round = bracket.get('current_round', 'round_1')
        
        try:
            next_round = f"round_{int(current_round.split('_')[1]) + 1}"
        except (ValueError, IndexError):
            next_round = "round_2"
        
        if tournament.get('format') == '1v1':
            next_matches = self.create_1v1_bracket(winners_flat)
        else:
            next_matches = self.create_team_bracket(winners_flat, tournament.get('format', '2v2'))
        
        # Инициализируем счёт для новых матчей
        for new_match in next_matches:
            match_scores[new_match.get('match_id', 0)] = {'team1_wins': 0, 'team2_wins': 0}
        
        bracket[next_round] = next_matches
        bracket['current_round'] = next_round
        
        try:
            if tournament.get('id'):
                self.db.update_tournament_bracket(tournament['id'], bracket)
        except Exception as e:
            logger.error(f"Error updating bracket: {e}")
        
        # Определяем название следующего раунда
        if len(next_matches) == 1:
            round_name = "🏆 ФИНАЛ ТУРНИРА!"
        elif len(next_matches) == 2:
            round_name = "🎯 ПОЛУФИНАЛ!"
        else:
            round_name = f"⚔️ Следующий раунд"
        
        next_round_text = f"🔄 {round_name}\n\n"
        
        for i, match in enumerate(next_matches, 1):
            team1_names = await self.get_team_names(match.get('team1', []), context, chat_id)
            team2_names = await self.get_team_names(match.get('team2', []), context, chat_id) 
            next_round_text += f"Матч #{i}: {team1_names} 🆚 {team2_names}\n"
        
        next_round_text += f"\n🛡️ Модераторы: используйте /winner [номер] [1или2] для определения победителей!"
        
        await update.message.reply_text(next_round_text)
    
    async def finish_tournament_by_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Завершение турнира через команду"""
        chat_id = update.effective_chat.id
        tournament = active_tournaments.get(chat_id, {})
        
        # Определяем призеров
        winners_data = self.determine_tournament_winners(tournament)
        
        # Начисляем награды
        winners_text = ""
        for i, winner_data in enumerate(winners_data[:3]):
            place = i + 1
            team = winner_data.get('team', [])
            
            xp_rewards = {1: 100, 2: 75, 3: 50}
            xp = xp_rewards.get(place, 0)
            
            for user_id in team:
                try:
                    self.db.add_tournament_win(user_id, place)
                except Exception as e:
                    logger.error(f"Error adding win for user {user_id}: {e}")
            
            team_names = await self.get_team_names(team, context, chat_id)
            
            if place == 1:
                winners_text += f"🥇 1 место — {team_names} (+{xp} XP)\n"
            elif place == 2:
                winners_text += f"🥈 2 место — {team_names} (+{xp} XP)\n"
            elif place == 3:
                winners_text += f"🥉 3 место — {team_names} (+{xp} XP)\n"
        
        # Завершаем турнир
        try:
            if tournament.get('id'):
                self.db.finish_tournament(tournament['id'])
        except Exception as e:
            logger.error(f"Error finishing tournament: {e}")
        
        # Очищаем данные матчей
        bracket = tournament.get('bracket', {})
        for round_key in bracket.keys():
            if round_key.startswith('round_'):
                for match in bracket[round_key]:
                    match_id = match.get('match_id', 0)
                    if match_id in match_scores:
                        del match_scores[match_id]
        
        if chat_id in active_tournaments:
            del active_tournaments[chat_id]
        if chat_id in user_participation_tracker:
            del user_participation_tracker[chat_id]
        
        format_name = TOURNAMENT_FORMATS.get(tournament.get('format', '1v1'), tournament.get('format', '1v1'))
        final_message = f"""🏁 Турнир {format_name} завершён!

{winners_text}

🎉 Поздравляем победителей!
🎮 Спасибо всем за участие!

XP добавлен в профили игроков

📆 Хотите новый турнир? Используйте /start"""
        
        await update.message.reply_text(final_message)

    async def new_member(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик добавления бота в группу"""
        for member in update.message.new_chat_members:
            if member.id == context.bot.id:
                try:
                    await self.send_welcome_message(update, context)
                except Exception as e:
                    logger.error(f"Ошибка в new_member: {e}")
                    keyboard = [
                        [
                            InlineKeyboardButton("🎮 Начать турнир", callback_data="start_tournament"),
                            InlineKeyboardButton("🗓 Запланировать", callback_data="schedule_tournament")
                        ]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="🤖 Добро пожаловать! Я помогу организовать турниры по Brawl Stars!",
                        reply_markup=reply_markup
                    )
                break


async def setup_bot_commands(application):
    """Настройка команд бота для автокомплита"""
    # Обновленный список команд с новой командой /help_win
    updated_commands = BOT_COMMANDS + [
        ("help_win", "Помощь по определению победителей (для модераторов)")
    ]
    commands = [BotCommand(command, description) for command, description in updated_commands]
    await application.bot.set_my_commands(commands)


def main():
    """Главная функция запуска бота"""
    # Создание приложения
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Создание экземпляра бота
    bot = TournamentBot()

    # Регистрация обработчиков команд
    application.add_handler(CommandHandler("winner", bot.winner_command))
    application.add_handler(CommandHandler("matches", bot.matches_command))  
    application.add_handler(CommandHandler("bracket", bot.bracket_command))
    application.add_handler(CommandHandler("help_win", bot.help_win_command))  # Новая команда
    application.add_handler(CommandHandler("top", bot.top_command))
    application.add_handler(CommandHandler("start", bot.start_command))
    application.add_handler(CommandHandler("profile", bot.profile_command))
    application.add_handler(CommandHandler("setcups", bot.setcups_command))
    application.add_handler(CommandHandler("setclan", bot.setclan_command))
    application.add_handler(CommandHandler("ranks", bot.ranks_command))
    application.add_handler(CommandHandler("giverole", bot.giverole_command))
    application.add_handler(CommandHandler("help", bot.help_command))
    application.add_handler(CommandHandler("stats", bot.stats_command))
    
    # Регистрация обработчика callback-запросов
    application.add_handler(CallbackQueryHandler(bot.button_callback))
    
    # Регистрация обработчика ответов на опросы
    application.add_handler(PollAnswerHandler(bot.poll_answer))
    
    # Регистрация обработчика новых участников
    application.add_handler(MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS, 
        bot.new_member
    ))

    # Настройка команд бота для автодополнения
    async def post_init(app):
        await setup_bot_commands(app)
    
    application.post_init = post_init
    
    # Запуск бота
    print("🤖 Brawl Stars Tournament Bot запущен!")
    print("📋 Команды настроены для автодополнения")
    print("✅ Все исправления внедрены:")
    print("   • Исправлена ошибка KeyError: 'current_round'")
    print("   • Добавлены fallback методы для отсутствующих функций БД")
    print("   • Поддержка до 6 команд для турниров 2v2/3v3")
    print("   • Исправлено отображение роли Owner")
    print("   • Убрано дублирование режимов в сообщениях")
    print("   • Улучшена обработка ошибок с try/except блоками")
    print("   • Добавлена функция get_rank_by_xp()")
    print("   • Исправлена опечатка InlineKeyboardButton")
    print(f"   • Роль 'owner' зарезервирована для пользователя ID: {OWNER_ID}")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

Thread(target=run_web, daemon=True).start()

if __name__ == '__main__':
    main()
