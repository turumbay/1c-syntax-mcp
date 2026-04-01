#!/usr/bin/env python3
"""MCP сервер для проверки синтаксиса 1С."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent


class SyntaxIndex:
    """Индекс для быстрого поиска по синтаксису."""
    
    def __init__(self):
        self.tree: Dict[str, Any] = {}
        self.flat_index: Dict[str, Any] = {}  # Плоский индекс для быстрого поиска
        
    def load(self, json_path: str):
        """Загрузить дерево синтаксиса из JSON."""
        with open(json_path, 'r', encoding='utf-8') as f:
            self.tree = json.load(f)
        self._build_flat_index(self.tree)
    
    def _build_flat_index(self, node: Dict[str, Any], path: str = ""):
        """Построить плоский индекс для быстрого поиска."""
        if not isinstance(node, dict):
            return
            
        node_name = node.get('name', '')
        node_type = node.get('type', '')
        
        # Создаем ключ для индекса
        if node_type in ['ctor', 'method', 'property']:
            # Извлекаем имена из локализованных строк
            details = node.get('details', {})
            localized = details.get('localized_name', {})
            ru_name = localized.get('ru', '')
            en_name = localized.get('en', '')
            
            # Извлекаем чистое имя функции (без параметров)
            ru_clean = ru_name.split('(')[0].strip() if ru_name else ''
            en_clean = en_name.split('(')[0].strip() if en_name else ''
            
            # Добавляем в индекс по русскому имени
            if ru_clean:
                key = ru_clean.lower()
                if key not in self.flat_index:
                    self.flat_index[key] = []
                self.flat_index[key].append({
                    'path': path,
                    'node': node,
                    'name_ru': ru_clean,
                    'name_en': en_clean,
                    'type': node_type
                })
            
            # Добавляем в индекс по английскому имени
            if en_clean:
                key = en_clean.lower()
                if key not in self.flat_index:
                    self.flat_index[key] = []
                self.flat_index[key].append({
                    'path': path,
                    'node': node,
                    'name_ru': ru_clean,
                    'name_en': en_clean,
                    'type': node_type
                })
        
        # Рекурсивно обрабатываем детей
        children = node.get('children', [])
        for child in children:
            child_path = f"{path}/{node_name}" if path else node_name
            self._build_flat_index(child, child_path)
    
    def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Поиск по имени функции/метода."""
        query_lower = query.lower()
        results = []
        
        # Точное совпадение
        if query_lower in self.flat_index:
            results.extend(self.flat_index[query_lower])
        
        # Частичное совпадение
        if len(results) < limit:
            for key, items in self.flat_index.items():
                if query_lower in key and key != query_lower:
                    results.extend(items)
                    if len(results) >= limit:
                        break
        
        return results[:limit]
    
    def get_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Получить информацию по точному имени."""
        name_lower = name.lower()
        if name_lower in self.flat_index:
            items = self.flat_index[name_lower]
            return items[0] if items else None
        return None
    
    def suggest_completions(self, prefix: str, limit: int = 10) -> List[str]:
        """Предложить автодополнение по префиксу."""
        prefix_lower = prefix.lower()
        suggestions = []
        
        for key in self.flat_index.keys():
            if key.startswith(prefix_lower):
                items = self.flat_index[key]
                for item in items:
                    suggestions.append(item['name_ru'])
                    if len(suggestions) >= limit:
                        return suggestions
        
        return suggestions


def extract_hbk(hbk_path: str, output_dir: str) -> tuple[bool, str]:
    """
    Распаковывает .hbk файл используя 7z.
    
    Файлы .hbk являются ZIP архивами с дополнительными данными.
    
    Args:
        hbk_path: путь к .hbk файлу
        output_dir: директория для распаковки
        
    Returns:
        tuple[bool, str]: (успех, сообщение)
    """
    import subprocess
    import shutil
    
    # Проверяем наличие 7z
    if not shutil.which('7z') and not shutil.which('7za'):
        return False, "Ошибка: 7z не установлен. Установите: sudo apt install p7zip-full"
    
    if not os.path.exists(hbk_path):
        return False, f"Ошибка: файл {hbk_path} не найден"
    
    try:
        # Создаем директорию для распаковки
        os.makedirs(output_dir, exist_ok=True)
        
        # Распаковываем с помощью 7z
        cmd = ['7z', 'x', '-y', f'-o{output_dir}', hbk_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            return False, f"Ошибка при распаковке 7z: {result.stderr}"
        
        # Подсчитываем количество файлов
        files_count = sum(1 for _ in Path(output_dir).rglob('*') if _.is_file())
        
        file_size = os.path.getsize(hbk_path)
        
        message = f"✓ Успешно распаковано в: {output_dir}\n"
        message += f"  Размер архива: {file_size} байт\n"
        message += f"  Извлечено файлов: {files_count}"
        
        return True, message
            
    except Exception as e:
        return False, f"Ошибка: {e}"


def find_and_extract_syntax_hbk(script_dir: Path) -> tuple[bool, str]:
    """
    Находит и распаковывает shcntx_ru.hbk из последней версии 1С.
    
    Returns:
        tuple[bool, str]: (успех, сообщение)
    """
    # Ищем последнюю версию 1С
    base_paths = [
        Path("/opt/1cv8/x86_64"),
        Path("/opt/1C/v8.3/x86_64"),
        Path("/opt/1C/v8.5/x86_64")
    ]
    
    version_path = None
    for base_path in base_paths:
        if not base_path.exists():
            continue
        
        # Получаем все директории с версиями
        version_dirs = []
        for item in base_path.iterdir():
            if item.is_dir() and item.name[0].isdigit():
                version_dirs.append(item)
        
        if version_dirs:
            # Сортируем по версии и берем последнюю
            version_dirs.sort(key=lambda x: [int(p) for p in x.name.split('.')])
            version_path = version_dirs[-1]
            break
    
    if not version_path:
        return False, "Не найдена установленная версия 1С"
    
    # Ищем shcntx_ru.hbk
    hbk_file = version_path / "shcntx_ru.hbk"
    if not hbk_file.exists():
        return False, f"Файл shcntx_ru.hbk не найден в {version_path}"
    
    print(f"Найден файл: {hbk_file}", flush=True)
    
    # Распаковываем
    output_dir = script_dir / "extracted_syntax"
    success, message = extract_hbk(str(hbk_file), str(output_dir))
    
    if not success:
        return False, message
    
    print(message, flush=True)
    return True, str(output_dir)


def build_syntax_index(extracted_dir: Path, output_json: Path) -> bool:
    """
    Строит индекс синтаксиса из распакованных файлов.
    
    Returns:
        bool: успех операции
    """
    import re
    
    if not extracted_dir.exists():
        return False
    
    objects_path = extracted_dir / "objects"
    if not objects_path.exists():
        print(f"Ошибка: директория {objects_path} не найдена", flush=True)
        return False
    
    print(f"Построение индекса из {objects_path}...", flush=True)
    
    def parse_st(file_path):
        """Parse .st file to get ru and en strings."""
        try:
            with open(file_path, 'r', encoding='utf-8-sig') as f:
                content = f.read()
        except Exception:
            return "", ""
        
        ru_match = re.search(r'\"ru\"[^,]*,[^,]*,[^,]*,[^,]*,\"([^\"]*)\"', content)
        en_match = re.search(r'\"en\"[^,]*,[^,]*,[^,]*,[^,]*,\"([^\"]*)\"', content)
        ru_str = ru_match.group(1) if ru_match else ""
        en_str = en_match.group(1) if en_match else ""
        return ru_str, en_str
    
    def parse_html(file_path):
        """Parse HTML file to extract syntax, parameters, return value, and description."""
        if not os.path.exists(file_path):
            return {}
        try:
            with open(file_path, 'r', encoding='utf-8-sig') as f:
                content = f.read()
        except Exception:
            return {}
        
        def extract_section(section_name):
            pattern = rf'{section_name}:</p>(.*?)(?=<p class="|<h1|<div|$)'
            match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
            if match:
                return match.group(1).strip()
            return ""
        
        return {
            "syntax": extract_section("Синтаксис"),
            "parameters": extract_section("Параметры"),
            "return_value": extract_section("Возвращаемое значение"),
            "description": extract_section("Описание")
        }
    
    def process_object(object_path, object_name):
        """Process an object directory and return its node."""
        node = {
            "name": object_name,
            "type": "object",
            "children": [],
            "details": {}
        }
        
        object_html = os.path.join(object_path, f"{object_name}.html")
        if os.path.exists(object_html):
            obj_details = parse_html(object_html)
            if obj_details:
                node["details"] = obj_details
        
        for category in ['ctors', 'methods', 'properties']:
            category_path = os.path.join(object_path, category)
            if os.path.isdir(category_path):
                category_node = {
                    "name": category,
                    "type": "category",
                    "children": []
                }
                
                def process_category_dir(dir_path):
                    for file_name in os.listdir(dir_path):
                        file_path = os.path.join(dir_path, file_name)
                        
                        if file_name.endswith('.st'):
                            method_id = file_name[:-3]
                            st_file = file_path
                            html_file = os.path.join(dir_path, method_id + '.html')
                            
                            ru_str, en_str = parse_st(st_file)
                            html_details = parse_html(html_file) if os.path.exists(html_file) else {}
                            
                            display_name = ru_str if ru_str else method_id
                            
                            leaf_node = {
                                "id": method_id,
                                "name": display_name,
                                "type": category[:-1],
                                "details": {
                                    "localized_name": {
                                        "ru": ru_str,
                                        "en": en_str
                                    },
                                    "signature": html_details.get("syntax", ""),
                                    "parameters": html_details.get("parameters", ""),
                                    "return_value": html_details.get("return_value", ""),
                                    "description": html_details.get("description", "")
                                }
                            }
                            category_node["children"].append(leaf_node)
                        
                        elif os.path.isdir(file_path) and file_name not in ['__categories__'] and not file_name.startswith('.') and not file_name.endswith('.html'):
                            process_category_dir(file_path)
                
                process_category_dir(category_path)
                
                if category_node["children"]:
                    node["children"].append(category_node)
        
        for item in os.listdir(object_path):
            item_path = os.path.join(object_path, item)
            if os.path.isdir(item_path) and item not in ['ctors', 'methods', 'properties', '__categories__'] and not item.startswith('.') and not item.endswith('.html'):
                subobject_node = process_object(item_path, item)
                node["children"].append(subobject_node)
        
        return node
    
    try:
        root = {
            "name": "Syntax Assistant",
            "type": "root",
            "children": []
        }
        
        items = list(objects_path.iterdir())
        total = len(items)
        
        for idx, item in enumerate(items, 1):
            if item.is_dir() and item.name not in ['__categories__'] and not item.name.startswith('.'):
                if idx % 10 == 0:
                    print(f"  Обработано {idx}/{total} объектов...", flush=True)
                obj_node = process_object(str(item), item.name)
                root["children"].append(obj_node)
        
        print(f"Сохранение индекса в {output_json}...", flush=True)
        with open(output_json, 'w', encoding='utf-8') as f:
            json.dump(root, f, ensure_ascii=False, indent=2)
        
        print(f"✓ Индекс успешно создан", flush=True)
        return True
        
    except Exception as e:
        print(f"Ошибка при построении индекса: {e}", flush=True)
        return False


# Создаем экземпляр сервера
app = Server("1c-syntax-mcp")

# Глобальный индекс
syntax_index = SyntaxIndex()


async def main():
    """Запуск MCP сервера."""
    script_dir = Path(__file__).parent
    json_path = script_dir / "syntax_tree.json"
    
    # Проверяем наличие индекса
    if not json_path.exists():
        print("Индекс не найден. Инициализация...", flush=True)
        
        # Находим и распаковываем shcntx_ru.hbk
        success, result = find_and_extract_syntax_hbk(script_dir)
        
        if not success:
            print(f"Ошибка инициализации: {result}", flush=True)
            print("Попытка использовать существующий индекс...", flush=True)
            if not json_path.exists():
                print(f"Ошибка: файл {json_path} не найден", flush=True)
                return
        else:
            extracted_dir = Path(result)
            
            # Строим индекс
            if not build_syntax_index(extracted_dir, json_path):
                print("Не удалось построить индекс из распакованных файлов", flush=True)
                if not json_path.exists():
                    print(f"Ошибка: файл {json_path} не найден", flush=True)
                    return
    
    print(f"Загрузка индекса из {json_path}...", flush=True)
    syntax_index.load(str(json_path))
    print(f"Индекс загружен: {len(syntax_index.flat_index)} элементов", flush=True)
    
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
