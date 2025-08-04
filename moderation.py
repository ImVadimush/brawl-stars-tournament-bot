# moderation.py
"""
Модуль системы модерации для Telegram-бота турниров Brawl Stars
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from telegram import Update, ChatMember, ChatPermissions
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters
from telegram.error import BadRequest, Forbidden

from database import DatabaseManager
from config import OWNER_ID

logger = logging.getLogger(__name__)

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

class ModerationManager:
    def __init__(self, db: DatabaseManager):
        self.db = db
        self.temp_restrictions = {}  # chat_id: {user_id: end_time}
        
    def init_moderation_tables(self):
        """Инициализация таблиц для модерации"""
        try:
            import sqlite3
            with sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.cursor()
                
                # Таблица предупреждений
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS warnings (
                        warning_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        chat_id INTEGER,
                        moderator_id INTEGER,
                        reason TEXT,
                        count INTEGER DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Таблица действий модерации
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS moderation_actions (
                        action_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chat_id INTEGER,
                        user_id INTEGER,
                        moderator_id INTEGER,
                        action_type TEXT,
                        reason TEXT,
                        duration TEXT,
                        end_time TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                conn.commit()
                logger.info("Moderation tables initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing moderation tables: {e}")

    async def check_moderator_permissions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Проверка прав модератора"""
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        
        # Владелец бота всегда имеет права
        if user_id == OWNER_ID:
            return True
        
        try:
            # Проверяем роль в базе данных
            user = self.db.get_user(user_id)
            if user and user.get('role') in ['moderator', 'admin', 'owner']:
                return True
            
            # Проверяем права администратора в чате
            member = await context.bot.get_chat_member(chat_id, user_id)
            if member.status in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
                return True
                
        except Exception as e:
            logger.error(f"Error checking moderator permissions: {e}")
        
        return False

    def parse_time_duration(self, duration_str: str) -> Optional[timedelta]:
        """Парсинг строки времени в timedelta"""
        if not duration_str:
            return None
            
        # Регулярное выражение для парсинга времени (например: 10m, 2h, 1d)
        time_regex = re.match(r'^(\d+)([mhd])$', duration_str.lower())
        if not time_regex:
            return None
        
        amount = int(time_regex.group(1))
        unit = time_regex.group(2)
        
        if unit == 'm':
            return timedelta(minutes=amount)
        elif unit == 'h':
            return timedelta(hours=amount)
        elif unit == 'd':
            return timedelta(days=amount)
        
        return None

    def add_warning(self, user_id: int, chat_id: int, moderator_id: int, reason: str) -> int:
        """Добавление предупреждения пользователю"""
        try:
            import sqlite3
            with sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.cursor()
                
                # Проверяем количество существующих предупреждений
                cursor.execute('''
                    SELECT COUNT(*) FROM warnings 
                    WHERE user_id = ? AND chat_id = ? 
                    AND created_at > datetime('now', '-30 days')
                ''', (user_id, chat_id))
                
                current_warnings = cursor.fetchone()[0]
                
                # Добавляем новое предупреждение
                cursor.execute('''
                    INSERT INTO warnings (user_id, chat_id, moderator_id, reason, count)
                    VALUES (?, ?, ?, ?, ?)
                ''', (user_id, chat_id, moderator_id, reason, current_warnings + 1))
                
                conn.commit()
                return current_warnings + 1
                
        except Exception as e:
            logger.error(f"Error adding warning: {e}")
            return 0

    def log_moderation_action(self, chat_id: int, user_id: int, moderator_id: int, 
                             action_type: str, reason: str, duration: str = None, 
                             end_time: datetime = None):
        """Логирование действия модерации"""
        try:
            import sqlite3
            with sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO moderation_actions 
                    (chat_id, user_id, moderator_id, action_type, reason, duration, end_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (chat_id, user_id, moderator_id, action_type, reason, duration, end_time))
                conn.commit()
        except Exception as e:
            logger.error(f"Error logging moderation action: {e}")

    async def mute_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда мута пользователя"""
        # Проверяем что команда используется в группе
        if update.effective_chat.type == 'private':
            await update.message.reply_text("❌ Эта команда доступна только в группах и каналах!")
            return
            
        if not await self.check_moderator_permissions(update, context):
            await update.message.reply_text("❌ У вас нет прав для использования этой команды!")
            return

        # Парсинг аргументов команды
        args = context.args if hasattr(context, 'args') and context.args else []
        
        # Если это русская команда !мут, парсим аргументы из текста
        if update.message.text and (update.message.text.startswith('!мут') or update.message.text.startswith('!mute')):
            args = update.message.text.split()[1:]  # Убираем саму команду
        
        if len(args) < 1:
            await update.message.reply_text(
                "❌ <b>Использование команды:</b>\n"
                "<code>/mute причина [время]</code> или <code>!мут причина [время]</code>\n\n"
                "📝 <b>Пример:</b> <code>/mute спам 30m</code>\n"
                "⏰ <b>Время:</b> m (минуты), h (часы), d (дни)\n"
                "💬 Ответьте на сообщение пользователя",
                parse_mode='HTML'
            )
            return

        try:
            # Получаем пользователя из упоминания
            if update.message.reply_to_message:
                target_user = update.message.reply_to_message.from_user
            else:
                await update.message.reply_text("❌ Ответьте на сообщение пользователя")
                return

            # Собираем причину и время
            reason_and_time = " ".join(args)
            parts = reason_and_time.rsplit(' ', 1)
            
            if len(parts) == 2 and self.parse_time_duration(parts[1]):
                reason = parts[0]
                duration_str = parts[1]
                duration = self.parse_time_duration(duration_str)
            else:
                reason = reason_and_time
                duration_str = "навсегда"
                duration = None

            # Проверяем, что не мутим администратора
            target_member = await context.bot.get_chat_member(update.effective_chat.id, target_user.id)
            if target_member.status in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
                await update.message.reply_text("❌ Нельзя замутить администратора!")
                return

            # Ограничиваем права пользователя
            permissions = ChatPermissions(
                can_send_messages=False,
                can_send_media_messages=False,
                can_send_polls=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False,
                can_change_info=False,
                can_invite_users=False,
                can_pin_messages=False
            )

            if duration:
                until_date = datetime.now() + duration
                await context.bot.restrict_chat_member(
                    chat_id=update.effective_chat.id,
                    user_id=target_user.id,
                    permissions=permissions,
                    until_date=until_date
                )
                time_text = f"на {duration_str}"
            else:
                await context.bot.restrict_chat_member(
                    chat_id=update.effective_chat.id,
                    user_id=target_user.id,
                    permissions=permissions
                )
                time_text = "навсегда"

            # Логируем действие
            self.log_moderation_action(
                update.effective_chat.id, target_user.id, update.effective_user.id,
                "mute", reason, duration_str, 
                datetime.now() + duration if duration else None
            )

            # Отправляем уведомление с HTML форматированием
            moderator_name = update.effective_user.first_name
            target_name = target_user.first_name
            
            message_text = (
                f"🔇 <b>Пользователь замучен</b>\n\n"
                f"👤 <b>Пользователь:</b> {target_name}\n"
                f"👮‍♂️ <b>Модератор:</b> {moderator_name}\n"
                f"⏱️ <b>Время:</b> {time_text}\n"
                f"📝 <b>Причина:</b> {reason}"
            )
            
            await update.message.reply_text(message_text, parse_mode='HTML')

        except BadRequest as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")
        except Exception as e:
            logger.error(f"Error in mute command: {e}")
            await update.message.reply_text("❌ Произошла ошибка при выполнении команды")

    async def ban_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда бана пользователя"""
        # Проверяем что команда используется в группе
        if update.effective_chat.type == 'private':
            await update.message.reply_text("❌ Эта команда доступна только в группах и каналах!")
            return
            
        if not await self.check_moderator_permissions(update, context):
            await update.message.reply_text("❌ У вас нет прав для использования этой команды!")
            return

        args = context.args if hasattr(context, 'args') and context.args else []
        
        # Если это русская команда !бан, парсим аргументы из текста
        if update.message.text and (update.message.text.startswith('!бан') or update.message.text.startswith('!ban')):
            args = update.message.text.split()[1:]

        try:
            # Получаем пользователя
            if update.message.reply_to_message:
                target_user = update.message.reply_to_message.from_user
            else:
                await update.message.reply_text("❌ Ответьте на сообщение пользователя")
                return

            # Парсим причину и время
            reason_and_time = " ".join(args) if args else "Нарушение правил"
            parts = reason_and_time.rsplit(' ', 1)
            
            if len(parts) == 2 and self.parse_time_duration(parts[1]):
                reason = parts[0]
                duration_str = parts[1]
                duration = self.parse_time_duration(duration_str)
            else:
                reason = reason_and_time or "Нарушение правил"
                duration_str = "навсегда"
                duration = None

            # Проверяем права целевого пользователя
            target_member = await context.bot.get_chat_member(update.effective_chat.id, target_user.id)
            if target_member.status in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
                await update.message.reply_text("❌ Нельзя забанить администратора!")
                return

            # Баним пользователя
            if duration:
                until_date = datetime.now() + duration
                await context.bot.ban_chat_member(
                    chat_id=update.effective_chat.id,
                    user_id=target_user.id,
                    until_date=until_date,
                    revoke_messages=True
                )
                time_text = f"на {duration_str}"
            else:
                await context.bot.ban_chat_member(
                    chat_id=update.effective_chat.id,
                    user_id=target_user.id,
                    revoke_messages=True
                )
                time_text = "навсегда"

            # Логируем действие
            self.log_moderation_action(
                update.effective_chat.id, target_user.id, update.effective_user.id,
                "ban", reason, duration_str,
                datetime.now() + duration if duration else None
            )

            # Отправляем уведомление с HTML форматированием
            moderator_name = update.effective_user.first_name
            target_name = target_user.first_name
            
            message_text = (
                f"🔨 <b>Пользователь забанен</b>\n\n"
                f"👤 <b>Пользователь:</b> {target_name}\n"
                f"👮‍♂️ <b>Модератор:</b> {moderator_name}\n"
                f"⏱️ <b>Время:</b> {time_text}\n"
                f"📝 <b>Причина:</b> {reason}"
            )
            
            await update.message.reply_text(message_text, parse_mode='HTML')

        except BadRequest as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")
        except Exception as e:
            logger.error(f"Error in ban command: {e}")
            await update.message.reply_text("❌ Произошла ошибка при выполнении команды")

    async def kick_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда кика пользователя"""
        # Проверяем что команда используется в группе
        if update.effective_chat.type == 'private':
            await update.message.reply_text("❌ Эта команда доступна только в группах и каналах!")
            return
            
        if not await self.check_moderator_permissions(update, context):
            await update.message.reply_text("❌ У вас нет прав для использования этой команды!")
            return

        args = context.args if hasattr(context, 'args') and context.args else []
        
        # Если это русская команда !кик, парсим аргументы из текста
        if update.message.text and (update.message.text.startswith('!кик') or update.message.text.startswith('!kick')):
            args = update.message.text.split()[1:]

        try:
            # Получаем пользователя
            if update.message.reply_to_message:
                target_user = update.message.reply_to_message.from_user
                reason = " ".join(args) or "Нарушение правил"
            else:
                await update.message.reply_text("❌ Ответьте на сообщение пользователя")
                return

            # Проверяем права целевого пользователя
            target_member = await context.bot.get_chat_member(update.effective_chat.id, target_user.id)
            if target_member.status in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
                await update.message.reply_text("❌ Нельзя кикнуть администратора!")
                return

            # Кикаем пользователя (бан + разбан)
            await context.bot.ban_chat_member(
                chat_id=update.effective_chat.id,
                user_id=target_user.id,
                revoke_messages=False
            )
            
            await context.bot.unban_chat_member(
                chat_id=update.effective_chat.id,
                user_id=target_user.id
            )

            # Логируем действие
            self.log_moderation_action(
                update.effective_chat.id, target_user.id, update.effective_user.id,
                "kick", reason
            )

            # Отправляем уведомление с HTML форматированием
            moderator_name = update.effective_user.first_name
            target_name = target_user.first_name
            
            message_text = (
                f"👢 <b>Пользователь кикнут</b>\n\n"
                f"👤 <b>Пользователь:</b> {target_name}\n"
                f"👮‍♂️ <b>Модератор:</b> {moderator_name}\n"
                f"📝 <b>Причина:</b> {reason}"
            )
            
            await update.message.reply_text(message_text, parse_mode='HTML')

        except BadRequest as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")
        except Exception as e:
            logger.error(f"Error in kick command: {e}")
            await update.message.reply_text("❌ Произошла ошибка при выполнении команды")

    async def warn_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда предупреждения пользователю"""
        # Проверяем что команда используется в группе
        if update.effective_chat.type == 'private':
            await update.message.reply_text("❌ Эта команда доступна только в группах и каналах!")
            return
            
        if not await self.check_moderator_permissions(update, context):
            await update.message.reply_text("❌ У вас нет прав для использования этой команды!")
            return

        args = context.args if hasattr(context, 'args') and context.args else []
        
        # Если это русская команда !пред, парсим аргументы из текста
        if update.message.text and (update.message.text.startswith('!пред') or update.message.text.startswith('!warn')):
            args = update.message.text.split()[1:]

        try:
            # Получаем пользователя
            if update.message.reply_to_message:
                target_user = update.message.reply_to_message.from_user
                reason = " ".join(args) or "Нарушение правил"
            else:
                await update.message.reply_text("❌ Ответьте на сообщение пользователя")
                return

            # Проверяем права целевого пользователя
            target_member = await context.bot.get_chat_member(update.effective_chat.id, target_user.id)
            if target_member.status in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
                await update.message.reply_text("❌ Нельзя предупредить администратора!")
                return

            # Добавляем предупреждение
            warning_count = self.add_warning(
                target_user.id, update.effective_chat.id, 
                update.effective_user.id, reason
            )

            # Отправляем уведомление с HTML форматированием
            moderator_name = update.effective_user.first_name
            target_name = target_user.first_name
            
            message_text = (
                f"⚠️ <b>Предупреждение выдано</b>\n\n"
                f"👤 <b>Пользователь:</b> {target_name}\n"
                f"👮‍♂️ <b>Модератор:</b> {moderator_name}\n"
                f"📊 <b>Предупреждений:</b> {warning_count}/3\n"
                f"📝 <b>Причина:</b> {reason}"
            )
            
            await update.message.reply_text(message_text, parse_mode='HTML')

            # Автоматический мут на 3-м предупреждении
            if warning_count >= 3:
                permissions = ChatPermissions(
                    can_send_messages=False,
                    can_send_media_messages=False,
                    can_send_polls=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False,
                    can_change_info=False,
                    can_invite_users=False,
                    can_pin_messages=False
                )

                until_date = datetime.now() + timedelta(days=1)
                await context.bot.restrict_chat_member(
                    chat_id=update.effective_chat.id,
                    user_id=target_user.id,
                    permissions=permissions,
                    until_date=until_date
                )

                # Логируем автоматический мут
                self.log_moderation_action(
                    update.effective_chat.id, target_user.id, context.bot.id,
                    "auto_mute", "3 предупреждения", "1d", until_date
                )

                auto_mute_text = (
                    f"🔇 <b>АВТОМАТИЧЕСКИЙ МУТ</b>\n\n"
                    f"👤 <b>{target_name}</b> получил мут на 1 день за 3 предупреждения!"
                )
                
                await update.message.reply_text(auto_mute_text, parse_mode='HTML')

        except BadRequest as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")
        except Exception as e:
            logger.error(f"Error in warn command: {e}")
            await update.message.reply_text("❌ Произошла ошибка при выполнении команды")

    async def show_moderation_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показ справки по командам модерации"""
        if not await self.check_moderator_permissions(update, context):
            await update.message.reply_text("❌ У вас нет прав для использования этой команды!")
            return

        help_text = """🛡️ <b>КОМАНДЫ МОДЕРАЦИИ</b>

📌 <b>Доступные команды:</b>

🔇 <b>Заглушить пользователя:</b>
<code>/mute причина [время]</code>
<code>!мут причина [время]</code>

🔨 <b>Забанить пользователя:</b>
<code>/ban причина [время]</code>
<code>!бан причина [время]</code>

👢 <b>Кикнуть пользователя:</b>
<code>/kick причина</code>
<code>!кик причина</code>

⚠️ <b>Выдать предупреждение:</b>
<code>/warn причина</code>
<code>!пред причина</code>

📋 <b>Справка по командам:</b>
<code>/moderation</code>

━━━━━━━━━━━━━━━━━━━━━━━━━

🔧 <b>Как использовать:</b>
1️⃣ Ответьте на сообщение нарушителя
2️⃣ Используйте команду с причиной
3️⃣ Для мута/бана можно указать время

⏰ <b>Формат времени:</b>
• <code>10m</code> - 10 минут
• <code>2h</code> - 2 часа  
• <code>1d</code> - 1 день

📝 <b>Примеры:</b>
<code>/mute спам 30m</code>
<code>!мут реклама 1h</code>
<code>/ban флуд 1d</code>
<code>!пред мат</code>

⚡ <b>Права:</b> только модераторы и администраторы"""

        await update.message.reply_text(help_text, parse_mode='HTML')

    def get_command_handlers(self):
        """Получение обработчиков команд модерации (только латинские команды)"""
        return [
            CommandHandler('mute', self.mute_user, filters=filters.ChatType.GROUPS),
            CommandHandler('ban', self.ban_user, filters=filters.ChatType.GROUPS),
            CommandHandler('kick', self.kick_user, filters=filters.ChatType.GROUPS),
            CommandHandler('warn', self.warn_user, filters=filters.ChatType.GROUPS),
            CommandHandler('moderation', self.show_moderation_help, filters=filters.ChatType.GROUPS),
        ]

    def get_message_handlers(self):
        """Получение обработчиков сообщений для модерации (русские команды с !)"""
        return [
            MessageHandler(filters.TEXT & filters.Regex(r'^!мут\b') & filters.ChatType.GROUPS, self.mute_user),
            MessageHandler(filters.TEXT & filters.Regex(r'^!бан\b') & filters.ChatType.GROUPS, self.ban_user),
            MessageHandler(filters.TEXT & filters.Regex(r'^!кик\b') & filters.ChatType.GROUPS, self.kick_user),
            MessageHandler(filters.TEXT & filters.Regex(r'^!пред\b') & filters.ChatType.GROUPS, self.warn_user),
        ]

# Функция для интеграции с основным ботом
def setup_moderation(application, db: DatabaseManager):
    """Настройка системы модерации"""
    moderation = ModerationManager(db)
    moderation.init_moderation_tables()
    
    # Добавляем обработчики команд (только латинские)
    for handler in moderation.get_command_handlers():
        application.add_handler(handler)
    
    # Добавляем обработчики сообщений (русские команды с !)
    for handler in moderation.get_message_handlers():
        application.add_handler(handler)
    
    logger.info("Moderation system initialized successfully")
    return moderation