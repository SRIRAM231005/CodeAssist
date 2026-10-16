import os
import sys

# Ensure backend root is on sys.path
BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from New_Model.adapters import (
    get_adapter,
    PythonAdapter,
    CAdapter,
    CppAdapter,
    ProjectSymbolIndex,
)


def test_python_adapter():
    py_code = """
import os
from math import sqrt

PI = 3.14159

def calculate(radius: float) -> float:
    \"\"\"Calculate area.\"\"\"
    val = helper(radius)
    return PI * val * val

class Geometry:
    def get_diameter(self, r):
        return r * 2
"""
    adapter = get_adapter("test.py")
    assert isinstance(adapter, PythonAdapter)

    fns = adapter.extract_functions(py_code, file_path="test.py")
    fn_names = [f.name for f in fns]
    assert "calculate" in fn_names
    assert "Geometry.get_diameter" in fn_names

    calc_fn = next(f for f in fns if f.name == "calculate")
    assert calc_fn.span[0] < calc_fn.span[1]

    calls = adapter.extract_calls(calc_fn.code)
    call_names = [c.callee_name for c in calls]
    assert "helper" in call_names

    chunks = adapter.extract_loose_chunks(py_code)
    chunk_types = [c.chunk_type for c in chunks]
    assert "import" in chunk_types
    assert "global_var" in chunk_types

    # Test hash normalization invariance
    code1 = "def f(x):\n    # comment 1\n    return x + 1\n"
    code2 = "def f(x):\n    # comment 2\n    return   x + 1\n"
    assert adapter.normalize_for_hash(code1) == adapter.normalize_for_hash(code2)
    print("[PASS] PythonAdapter tests passed!")


def test_c_adapter():
    c_code = """
#define BUFFER_SIZE 1024
typedef struct { int len; } Buffer;

static int local_helper(int n) {
    return n * 2;
}

int send_data(Buffer* buf, int bytes) {
    if (bytes <= 0) return -1;
    int scaled = local_helper(bytes);
    external_flush(scaled);
    buf->len = scaled;
    return 0;
}
"""
    adapter = get_adapter("network.c")
    assert isinstance(adapter, CAdapter)

    fns = adapter.extract_functions(c_code, file_path="network.c")
    assert len(fns) == 2

    helper = next(f for f in fns if f.name == "local_helper")
    assert helper.is_static is True
    assert helper.span == (5, 7)

    sender = next(f for f in fns if f.name == "send_data")
    assert sender.is_static is False

    calls = adapter.extract_calls(sender.code)
    call_names = [c.callee_name for c in calls]
    assert "local_helper" in call_names
    assert "external_flush" in call_names

    chunks = adapter.extract_loose_chunks(c_code)
    assert any(c.chunk_type == "macro" and "BUFFER_SIZE" in c.name for c in chunks)
    assert any(c.chunk_type == "struct" for c in chunks)

    # Comments should not alter hash
    c1 = "int f(int x) { /* note */ return x + 1; }"
    c2 = "int f(int x) {\n    // line note\n    return x + 1;\n}"
    assert adapter.normalize_for_hash(c1) == adapter.normalize_for_hash(c2)
    print("[PASS] CAdapter tests passed!")


def test_cpp_adapter():
    cpp_code = """
#define MAX_THREADS 8

namespace GPU {
    class Device {
    public:
        virtual void sync();
        int get_cores() {
            return helper_count();
        }
    };
}

void GPU::Device::sync() {
    flush_pipeline();
}

template <typename T>
T add(T a, T b) { return a + b; }
"""
    adapter = get_adapter("gpu_device.cpp")
    assert isinstance(adapter, CppAdapter)

    fns = adapter.extract_functions(cpp_code, file_path="gpu_device.cpp")
    fn_names = [f.name for f in fns]

    assert "GPU::Device::get_cores" in fn_names
    assert "GPU::Device::sync" in fn_names
    assert "add" in fn_names

    cores_fn = next(f for f in fns if f.name == "GPU::Device::get_cores")
    calls = adapter.extract_calls(cores_fn.code)
    assert any(c.callee_name == "helper_count" for c in calls)

    chunks = adapter.extract_loose_chunks(cpp_code)
    assert any(c.chunk_type == "class" and "Device" in c.name for c in chunks)
    assert any(c.chunk_type == "template" for c in chunks)

    print("[PASS] CppAdapter tests passed!")


def test_project_symbol_index():
    index = ProjectSymbolIndex()
    c_adapter = CAdapter()

    file_a = """
static int helper(int x) { return x + 1; }
int public_a(int x) { return helper(x); }
"""
    file_b = """
static int helper(int x) { return x * 10; }
int public_b(int x) { return helper(x); }
int common_task(void) { return 42; }
"""
    file_c = """
int common_task(void) { return 99; }
"""
    index.index_file("file_a.c", file_a, c_adapter)
    index.index_file("file_b.c", file_b, c_adapter)
    index.index_file("file_c.c", file_c, c_adapter)

    # 1. Static function in file_a resolves to file_a's helper
    res_a = index.resolve_call(caller_file="file_a.c", callee_name="helper")
    assert res_a.status == "resolved"
    assert res_a.target.file_path == "file_a.c"
    assert res_a.target.is_static is True

    # 2. Static function in file_b resolves to file_b's helper
    res_b = index.resolve_call(caller_file="file_b.c", callee_name="helper")
    assert res_b.status == "resolved"
    assert res_b.target.file_path == "file_b.c"

    # 3. From file_c, 'helper' is static in other files -> unresolved / not visible
    res_c = index.resolve_call(caller_file="file_c.c", callee_name="helper")
    assert res_c.status == "unresolved"

    # 4. 'public_a' is global, called from file_b -> resolves to file_a
    res_pub = index.resolve_call(caller_file="file_b.c", callee_name="public_a")
    assert res_pub.status == "resolved"
    assert res_pub.target.file_path == "file_a.c"

    # 5. 'common_task' is defined in both file_b and file_c -> ambiguous
    res_amb = index.resolve_call(caller_file="main.c", callee_name="common_task")
    assert res_amb.status == "ambiguous"
    assert len(res_amb.candidates) == 2

    print("[PASS] ProjectSymbolIndex tests passed!")


if __name__ == "__main__":
    test_python_adapter()
    test_c_adapter()
    test_cpp_adapter()
    test_project_symbol_index()
    print("\nALL STAGE 1 TESTS PASSED SUCCESSFULLY!")
