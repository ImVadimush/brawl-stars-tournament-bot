# database.py
"""
Модуль для работы с базой данных SQLite
"""

import sqlite3
import json
from typing import Dict, List, Optional, Tuple
from config import DATABASE_PATH, XP_REWARDS

class DatabaseManager:
    def __init__(self):
        self.db_path = DATABASE_PATH
        self.init_database()
    
    def init_database(self):
        """Инициализация базы данных и создание таблиц"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Таблица пользователей
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    role TEXT DEFAULT 'user',
                    wins INTEGER DEFAULT 0,
                    participations INTEGER DEFAULT 0,
                    xp INTEGER DEFAULT 0,
                    trophies INTEGER DEFAULT 0,
                    clan TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Таблица турниров
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tournaments (
                    tournament_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER,
                    format TEXT,
                    wins_needed INTEGER,
                    modes TEXT,
                    maps TEXT,
                    participants TEXT,
                    bracket TEXT,
                    status TEXT DEFAULT 'registration',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    finished_at TIMESTAMP
                )
            ''')
            
            # Таблица запланированных турниров
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS scheduled_tournaments (
                    schedule_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER,
                    poll_message_id INTEGER,
                    participants TEXT,
                    scheduled_time TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Таблица статистики боев
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS match_history (
                    match_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tournament_id INTEGER,
                    round_name TEXT,
                    team1 TEXT,
                    team2 TEXT,
                    winner TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (tournament_id) REFERENCES tournaments (tournament_id)
                )
            ''')
            
            conn.commit()
    
    def add_user(self, user_id: int, username: str = None, first_name: str = None) -> bool:
        """Добавление нового пользователя"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR IGNORE INTO users (user_id, username, first_name)
                    VALUES (?, ?, ?)
                ''', (user_id, username, first_name))
                conn.commit()
                return True
        except Exception as e:
            print(f"Ошибка добавления пользователя: {e}")
            return False
    
    def get_user(self, user_id: int) -> Optional[Dict]:
        """Получение информации о пользователе"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
                result = cursor.fetchone()
                
                if result:
                    columns = [desc[0] for desc in cursor.description]
                    return dict(zip(columns, result))
                return None
        except Exception as e:
            print(f"Ошибка получения пользователя: {e}")
            return None
    
    def update_user_role(self, user_id: int, role: str) -> bool:
        """Обновление роли пользователя"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE users SET role = ? WHERE user_id = ?
                ''', (role, user_id))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            print(f"Ошибка обновления роли: {e}")
            return False
    
    def update_user_trophies(self, user_id: int, trophies: int) -> bool:
        """Обновление кубков пользователя"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE users SET trophies = ? WHERE user_id = ?
                ''', (trophies, user_id))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            print(f"Ошибка обновления кубков: {e}")
            return False
    
    def update_user_clan(self, user_id: int, clan: str) -> bool:
        """Обновление клана пользователя"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE users SET clan = ? WHERE user_id = ?
                ''', (clan, user_id))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            print(f"Ошибка обновления клана: {e}")
            return False
    
    def add_tournament_participation(self, user_id: int) -> bool:
        """Увеличение счетчика участий в турнирах"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE users SET participations = participations + 1 
                    WHERE user_id = ?
                ''', (user_id,))
                conn.commit()
                return True
        except Exception as e:
            print(f"Ошибка добавления участия: {e}")
            return False
    
    def add_tournament_win(self, user_id: int, place: int) -> bool:
        """Добавление победы и опыта пользователю"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                xp_reward = XP_REWARDS.get(place, 0)
                
                if place == 1:
                    cursor.execute('''
                        UPDATE users SET wins = wins + 1, xp = xp + ? 
                        WHERE user_id = ?
                    ''', (xp_reward, user_id))
                else:
                    cursor.execute('''
                        UPDATE users SET xp = xp + ? WHERE user_id = ?
                    ''', (xp_reward, user_id))
                
                conn.commit()
                return True
        except Exception as e:
            print(f"Ошибка добавления победы: {e}")
            return False
    
    def create_tournament(self, chat_id: int, format_type: str, wins_needed: int, 
                         modes: List[str], maps: Dict) -> int:
        """Создание нового турнира"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO tournaments (chat_id, format, wins_needed, modes, maps, participants, bracket)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (chat_id, format_type, wins_needed, json.dumps(modes), 
                      json.dumps(maps), json.dumps([]), json.dumps({})))
                conn.commit()
                return cursor.lastrowid
        except Exception as e:
            print(f"Ошибка создания турнира: {e}")
            return 0
    
    def get_tournament(self, tournament_id: int) -> Optional[Dict]:
        """Получение информации о турнире"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM tournaments WHERE tournament_id = ?', (tournament_id,))
                result = cursor.fetchone()
                
                if result:
                    columns = [desc[0] for desc in cursor.description]
                    tournament = dict(zip(columns, result))
                    
                    # Преобразование JSON полей обратно в объекты
                    tournament['modes'] = json.loads(tournament['modes'])
                    tournament['maps'] = json.loads(tournament['maps'])
                    tournament['participants'] = json.loads(tournament['participants'])
                    tournament['bracket'] = json.loads(tournament['bracket'])
                    
                    return tournament
                return None
        except Exception as e:
            print(f"Ошибка получения турнира: {e}")
            return None
    
    def update_tournament_participants(self, tournament_id: int, participants: List[int]) -> bool:
        """Обновление списка участников турнира"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE tournaments SET participants = ? WHERE tournament_id = ?
                ''', (json.dumps(participants), tournament_id))
                conn.commit()
                return True
        except Exception as e:
            print(f"Ошибка обновления участников: {e}")
            return False
    
    def update_tournament_bracket(self, tournament_id: int, bracket: Dict) -> bool:
        """Обновление турнирной сетки"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE tournaments SET bracket = ? WHERE tournament_id = ?
                ''', (json.dumps(bracket), tournament_id))
                conn.commit()
                return True
        except Exception as e:
            print(f"Ошибка обновления сетки: {e}")
            return False
    
    def finish_tournament(self, tournament_id: int) -> bool:
        """Завершение турнира"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE tournaments SET status = 'finished', finished_at = CURRENT_TIMESTAMP 
                    WHERE tournament_id = ?
                ''', (tournament_id,))
                conn.commit()
                return True
        except Exception as e:
            print(f"Ошибка завершения турнира: {e}")
            return False
    
    def add_scheduled_tournament(self, chat_id: int, poll_message_id: int, 
                               scheduled_time: str) -> int:
        """Добавление запланированного турнира"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO scheduled_tournaments (chat_id, poll_message_id, participants, scheduled_time)
                    VALUES (?, ?, ?, ?)
                ''', (chat_id, poll_message_id, json.dumps([]), scheduled_time))
                conn.commit()
                return cursor.lastrowid
        except Exception as e:
            print(f"Ошибка добавления запланированного турнира: {e}")
            return 0
    
    def update_scheduled_participants(self, schedule_id: int, participants: List[int]) -> bool:
        """Обновление участников запланированного турнира"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE scheduled_tournaments SET participants = ? WHERE schedule_id = ?
                ''', (json.dumps(participants), schedule_id))
                conn.commit()
                return True
        except Exception as e:
            print(f"Ошибка обновления запланированных участников: {e}")
            return False
    
    def get_total_users(self) -> int:
        """Получение общего количества пользователей"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT COUNT(*) FROM users')
                result = cursor.fetchone()
                return result[0] if result else 0
        except Exception as e:
            print(f"Ошибка получения количества пользователей: {e}")
            return 0
    
    def get_total_tournaments(self) -> int:
        """Получение общего количества турниров"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT COUNT(*) FROM tournaments')
                result = cursor.fetchone()
                return result[0] if result else 0
        except Exception as e:
            print(f"Ошибка получения количества турниров: {e}")
            return 0
    
    def get_finished_tournaments(self) -> int:
        """Получение количества завершенных турниров"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT COUNT(*) FROM tournaments WHERE status = ?', ('finished',))
                result = cursor.fetchone()
                return result[0] if result else 0
        except Exception as e:
            print(f"Ошибка получения количества завершенных турниров: {e}")
            return 0
    
    def get_user_stats(self, user_id: int) -> Dict:
        """Получение статистики пользователя"""
        user = self.get_user(user_id)
        if not user:
            return {}
        
        win_rate = 0
        if user['participations'] > 0:
            win_rate = round((user['wins'] / user['participations']) * 100)
        
        # Определение ранга по опыту
        xp = user['xp']
        if xp >= 1000:
            rank = "👑 Легенда"
        elif xp >= 500:
            rank = "🏆 Мастер"
        elif xp >= 200:
            rank = "🎖️ Эксперт"
        elif xp >= 50:
            rank = "🥉 Любитель"
        else:
            rank = "🙋 Новичок"
        
        return {
            'wins': user['wins'],
            'participations': user['participations'],
            'win_rate': win_rate,
            'xp': user['xp'],
            'rank': rank,
            'trophies': user['trophies'],
            'clan': user['clan'] or 'Без клана'
        }
    
    def get_top_users_by_trophies(self, limit=10):
        """Получение топа пользователей по кубкам"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT username, first_name, trophies
                    FROM users 
                    WHERE trophies > 0
                    ORDER BY trophies DESC 
                    LIMIT ?
                """, (limit,))
                
                results = cursor.fetchall()
                return [
                    {
                        'username': row[0] or row[1] or 'Неизвестный',
                        'trophies': row[2]
                    }
                    for row in results
                ]
        except Exception as e:
            print(f"Error getting top users by trophies: {e}")
            return []

    def get_top_users_by_experience(self, limit=10):
        """Получение топа пользователей по опыту"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT username, first_name, xp
                    FROM users 
                    WHERE xp > 0
                    ORDER BY xp DESC 
                    LIMIT ?
                """, (limit,))
                
                results = cursor.fetchall()
                top_users = []
                for row in results:
                    username = row[0] or row[1] or 'Неизвестный'
                    xp = row[2]
                    rank = self.get_rank_by_xp(xp)
                    top_users.append({
                        'username': username,
                        'xp': xp,
                        'rank': rank
                    })
                return top_users
        except Exception as e:
            print(f"Error getting top users by experience: {e}")
            return []

    def get_top_users_by_wins(self, limit=10):
        """Получение топа пользователей по победам"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT username, first_name, wins, participations
                    FROM users 
                    WHERE wins > 0
                    ORDER BY wins DESC 
                    LIMIT ?
                """, (limit,))
                
                results = cursor.fetchall()
                top_users = []
                for row in results:
                    username = row[0] or row[1] or 'Неизвестный'
                    wins = row[2]
                    participations = row[3]
                    win_rate = round((wins / participations * 100) if participations > 0 else 0, 1)
                    top_users.append({
                        'username': username,
                        'wins': wins,
                        'win_rate': win_rate
                    })
                return top_users
        except Exception as e:
            print(f"Error getting top users by wins: {e}")
            return []

def get_user_by_username(self, username: str) -> Optional[Dict]:
        """Получение пользователя по username"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
                result = cursor.fetchone()
                
                if result:
                    columns = [desc[0] for desc in cursor.description]
                    return dict(zip(columns, result))
                return None
        except Exception as e:
            print(f"Ошибка получения пользователя по username: {e}")
            return None

def get_top_users_by_participations(self, limit=10):
        """Получение топа пользователей по участиям"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT username, first_name, participations, wins
                    FROM users 
                    WHERE participations > 0
                    ORDER BY participations DESC 
                    LIMIT ?
                """, (limit,))
                
                results = cursor.fetchall()
                return [
                    {
                        'username': row[0] or row[1] or 'Неизвестный',
                        'participations': row[2],
                        'wins': row[3]
                    }
                    for row in results
                ]
        except Exception as e:
            print(f"Error getting top users by participations: {e}")
            return []

def get_rank_by_xp(self, xp):
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