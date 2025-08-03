# maps.py
"""
Модуль с картами и режимами игры Brawl Stars
"""

import random
from typing import Dict, List

# Карты для каждого режима игры
GAME_MAPS = {
    "⚽ Броубол": [
        {
            "name": "Трипл-дриблинг",
            "image": "https://i.imgur.com/example1.jpg",
            "description": "Классическая карта с центральным проходом"
        },
        {
            "name": "Дворовый чемпионат", 
            "image": "https://i.imgur.com/example2.jpg",
            "description": "Узкие проходы по краям поля"
        },
        {
            "name": "Зловредные поля",
            "image": "https://i.imgur.com/example3.jpg", 
            "description": "Пляжная тема с водными препятствиями"
        },
        {
            "name": "Пинбол",
            "image": "https://i.imgur.com/example4.jpg",
            "description": "Три прохода к воротам"
        },
        {
            "name": "Забег по крышам",
            "image": "https://i.imgur.com/example5.jpg",
            "description": "Множество отражающих стен"
        }
    ],
    
    "💎 Захват кристаллов": [
        {
            "name": "Роковая шахта",
            "image": "https://i.imgur.com/gem1.jpg",
            "description": "Классическая симметричная карта"
        },
        {
            "name": "Поганковая западня", 
            "image": "https://i.imgur.com/gem2.jpg",
            "description": "Узкие проходы в шахте"
        },
        {
            "name": "Вжух-вжух",
            "image": "https://i.imgur.com/gem3.jpg",
            "description": "Пещера с кристаллами"
        },
        {
            "name": "Открытая местность",
            "image": "https://i.imgur.com/gem4.jpg", 
            "description": "Шахтерские вагонетки как укрытие"
        },
        {
            "name": "Подрыв",
            "image": "https://i.imgur.com/gem5.jpg",
            "description": "Подводная тематика"
        }
    ],
    
    "🎯 Награда за поимку": [ 
        {
            "name": "Без отговорок",
            "image": "https://i.imgur.com/bounty1.jpg",
            "description": "Открытая карта для снайперов"
        },
        {
            "name": "Падающая звезда",
            "image": "https://i.imgur.com/bounty2.jpg",
            "description": "Венецианские каналы"
        },
        {
            "name": "Укрытие", 
            "image": "https://i.imgur.com/bounty3.jpg",
            "description": "Пустынная местность"
        },
        {
            "name": "Засуха",
            "image": "https://i.imgur.com/bounty4.jpg",
            "description": "Многоуровневая карта"
        },
        {
            "name": "Кремовый торт",
            "image": "https://i.imgur.com/bounty5.jpg",
            "description": "Густая трава повсюду"
        }
    ],
    
    "🔥 Горячая зона": [
        {
            "name": "Параллельная игра",
            "image": "https://i.imgur.com/hotzone1.jpg",
            "description": "Две параллельные зоны"
        },
        {
            "name": "Муравьиные бега",
            "image": "https://i.imgur.com/hotzone2.jpg", 
            "description": "Кольцевая зона в центре"
        },
        {
            "name": "Огненное кольцо",
            "image": "https://i.imgur.com/hotzone3.jpg",
            "description": "Две зоны по краям"
        },
        {
            "name": "На грани",
            "image": "https://i.imgur.com/hotzone4.jpg",
            "description": "Разделенная карта"
        },
        {
            "name": "Открыто!",
            "image": "https://i.imgur.com/hotzone5.jpg",
            "description": "Открытое пространство"
        }
    ],
    
    "💥 Одиночное столкновение (ШД)": [ 
        {
            "name": "Омут",
            "image": "https://i.imgur.com/showdown1.jpg",
            "description": "Классическая карта для выживания"
        },
        {
            "name": "Пылающий феникс",
            "image": "https://i.imgur.com/showdown2.jpg",
            "description": "Много травы и укрытий"
        },
        {
            "name": "Живописный утес",
            "image": "https://i.imgur.com/showdown3.jpg",
            "description": "Центральная область с ящиками"
        },
        {
            "name": "В чистом поле",
            "image": "https://i.imgur.com/showdown4.jpg",
            "description": "Множество водоемов"
        },
        {
            "name": "Зов воды",
            "image": "https://i.imgur.com/showdown5.jpg",
            "description": "Открытые равнины"
        }
    ],

    "👬 Дуо-ШД": [
        {
            "name": "Двойной беспредел",
            "image": "https://i.imgur.com/duoshowdown1.jpg",
            "description": "Карта для командной игры"
        },
        {
            "name": "Пещеры темноты",
            "image": "https://i.imgur.com/duoshowdown2.jpg",
            "description": "Множество укрытий для дуэтов"
        },
        {
            "name": "Заброшенный город",
            "image": "https://i.imgur.com/duoshowdown3.jpg",
            "description": "Городская местность"
        },
        {
            "name": "Лесные дебри",
            "image": "https://i.imgur.com/duoshowdown4.jpg",
            "description": "Густой лес с засадами"
        },
        {
            "name": "Арена чемпионов",
            "image": "https://i.imgur.com/duoshowdown5.jpg",
            "description": "Центральная арена для боев"
        }
    ],

    "💥 Нокаут": [ 
        {
            "name": "Обезвреживание",
            "image": "https://i.imgur.com/knockout1.jpg",
            "description": "Симметричная карта для тактических боев"
        },
        {
            "name": "Шип-лабиринт",
            "image": "https://i.imgur.com/knockout2.jpg",
            "description": "Лабиринт с множеством поворотов"
        },
        {
            "name": "Золотая рука",
            "image": "https://i.imgur.com/knockout3.jpg",
            "description": "Открытая карта с центральным укрытием"
        },
        {
            "name": "Склеп",
            "image": "https://i.imgur.com/knockout4.jpg",
            "description": "Темная карта с узкими проходами"
        },
        {
            "name": "Изумрудные равнины",
            "image": "https://i.imgur.com/knockout5.jpg",
            "description": "Зеленые поля с тактическими позициями"
        }
    ]
}

class MapManager:
    """Класс для управления картами и режимами"""
    
    def __init__(self):
        self.maps = GAME_MAPS
    
    def get_random_map(self, game_mode: str) -> Dict:
        """Получить случайную карту для указанного режима"""
        if game_mode in self.maps:
            return random.choice(self.maps[game_mode])
        return {}
    
    def get_random_maps_for_modes(self, game_modes: List[str]) -> Dict[str, Dict]:
        """Получить случайные карты для списка режимов"""
        selected_maps = {}
        for mode in game_modes:
            selected_maps[mode] = self.get_random_map(mode)
        return selected_maps
    
    def get_all_modes_for_format(self, format_type: str) -> List[str]:
        """Получить список доступных режимов для формата"""
        from config import GAME_MODES
        return GAME_MODES.get(format_type, [])
    
    def get_maps_for_mode(self, game_mode: str) -> List[Dict]:
        """Получить все карты для указанного режима"""
        return self.maps.get(game_mode, [])
    
    def format_map_info(self, game_mode: str, map_data: Dict) -> str:
        """Отформатировать информацию о карте"""
        if not map_data:
            return f"📍 **Режим:** {game_mode}\n🗺️ **Карта:** Не выбрана"
        
        return f"""📍 **Режим:** {game_mode}
🗺️ **Карта:** {map_data['name']}
📝 {map_data['description']}"""
    
    def format_selected_maps(self, selected_maps: Dict[str, Dict]) -> str:
        """Отформатировать информацию о всех выбранных картах"""
        if not selected_maps:
            return "🗺️ Карты не выбраны"
        
        formatted_maps = []
        for mode, map_data in selected_maps.items():
            formatted_maps.append(self.format_map_info(mode, map_data))
        
        return "\n\n".join(formatted_maps)
    
    def get_mode_emoji(self, game_mode: str) -> str:
        """Получить эмодзи для режима игры"""
        emoji_map = {
            "⚽ Броубол": "⚽",
            "💎 Захват кристаллов": "💎", 
            "🎯 Награда за поимку": "🎯",
            "🔥 Горячая зона": "🔥",
            "💥 Одиночное столкновение (ШД)": "💥",
            "👬 Дуо-ШД": "👬",
            "💥 Нокаут": "💥"
        }
        return emoji_map.get(game_mode, "🎮")

# Создаем глобальный экземпляр менеджера карт
map_manager = MapManager()