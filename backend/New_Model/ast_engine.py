import ast
import hashlib
import requests
from typing import Optional


def fetch_github_file(repo_url: str, file_path: str, branch: str = "main") -> str:
    clean_repo = repo_url.rstrip("/").replace("https://github.com/", "")
    raw_url = f"https://raw.githubusercontent.com/{clean_repo}/{branch}/{file_path}"
    response = requests.get(raw_url)

    if response.status_code == 200:
        return response.text
    elif response.status_code == 404:
        raw_url = f"https://raw.githubusercontent.com/{clean_repo}/master/{file_path}"
        response = requests.get(raw_url)
        if response.status_code == 200:
            return response.text
        raise ValueError(f"File not found: {file_path}")
    else:
        raise RuntimeError(f"GitHub request failed: {response.status_code}")


def compute_ast_hash(code: str) -> str:
    try:
        tree = ast.parse(code)
        return hashlib.sha256(ast.dump(tree).encode()).hexdigest()
    except Exception:
        return hashlib.sha256(code.encode()).hexdigest()


def extract_function_node(code: str, function_name: str) -> Optional[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return ast.get_source_segment(code, node)
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    if item.name == function_name or f"{node.name}.{item.name}" == function_name:
                        return ast.get_source_segment(code, item)
    return None


def extract_all_functions(code: str) -> list:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    functions = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            src = ast.get_source_segment(code, node)
            if src:
                functions.append({"name": node.name, "code": src})
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    src = ast.get_source_segment(code, item)
                    if src:
                        functions.append({"name": f"{node.name}.{item.name}", "code": src})
    return functions


def resolve_dependencies(code: str, function_name: str) -> dict:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {"local_calls": [], "imported_calls": [], "imports_map": {}}

    imports_map = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name
                imports_map[name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                name = alias.asname or alias.name
                imports_map[name] = module

    defined_functions = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            defined_functions.add(node.name)
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    defined_functions.add(item.name)
                    defined_functions.add(f"{node.name}.{item.name}")

    target_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            target_node = node
            break
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == function_name:
                    target_node = item
                    break

    if not target_node:
        return {"local_calls": [], "imported_calls": [], "imports_map": imports_map}

    called_names = set()
    for node in ast.walk(target_node):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called_names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called_names.add(node.func.attr)
                if isinstance(node.func.value, ast.Name):
                    called_names.add(node.func.value.id)
        elif isinstance(node, ast.Name):
            called_names.add(node.id)

    local_calls = []
    imported_calls = []
    for name in called_names:
        if name in defined_functions and name != function_name:
            local_calls.append(name)
        elif name in imports_map:
            imported_calls.append({"name": name, "module": imports_map[name]})

    return {
        "local_calls": list(set(local_calls)),
        "imported_calls": imported_calls,
        "imports_map": imports_map
    }


STDLIB_AND_COMMON = {
    "os", "sys", "json", "ast", "re", "math", "time", "datetime",
    "collections", "itertools", "functools", "pathlib", "typing",
    "abc", "io", "copy", "hashlib", "random", "string", "traceback",
    "requests", "flask", "django", "fastapi", "sqlalchemy", "pandas",
    "numpy", "torch", "transformers", "streamlit", "chromadb",
    "google", "anthropic", "openai", "redis", "celery", "pydantic"
}


def resolve_file_path_from_module(
    module: str,
    repo_url: str,
    branch: str = "main",
    base_path: str = ""
) -> Optional[str]:
    if not module:
        return None
    root = module.split(".")[0]
    if root in STDLIB_AND_COMMON:
        return None

    file_path = module.replace(".", "/") + ".py"

    if base_path:
        base = base_path.rsplit("/", 1)[0]
        candidate = f"{base}/{file_path}"
        try:
            fetch_github_file(repo_url, candidate, branch)
            return candidate
        except Exception:
            pass

    try:
        fetch_github_file(repo_url, file_path, branch)
        return file_path
    except Exception:
        pass

    return None
