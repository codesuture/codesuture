import subprocess
import sys
import os
import tempfile

def verify_fix(original_script_path, module_name, func_name, new_source, exc_type_name):
    print(f"[CodeSuture Sandbox] Testing fix for {module_name}.{func_name}...")

    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".py", encoding='utf-8') as f:
        f.write(new_source)
        source_path = f.name

    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".py", encoding='utf-8') as f:
        runner_code = f"""
import sys
import importlib

def main():
    with open({repr(source_path)}, 'r', encoding='utf-8') as f:
        new_source = f.read()

    new_module_code = compile(new_source, "<sandbox>", 'exec')
    new_func_code = None
    for const in new_module_code.co_consts:
        if type(const).__name__ == 'code' and const.co_name == {repr(func_name)}:
            new_func_code = const
            break

    if not new_func_code:
        print("SANDBOX ERROR: Could not find function in compiled new source")
        sys.exit(1)

    if {repr(module_name)} != '__main__':
        from codesuture.code_replacer import replace_function_code
        mod = importlib.import_module({repr(module_name)})
        # Find the function object inside the module
        parts = {repr(func_name)}.split('.')
        obj = mod
        for part in parts:
            obj = getattr(obj, part)
        replace_function_code(obj, new_func_code)

        # Now run the script
        with open({repr(original_script_path)}, 'r', encoding='utf-8') as script_file:
            source = script_file.read()
        code = compile(source, {repr(original_script_path)}, 'exec')
        globs = {{'__name__': '__main__', '__file__': {repr(original_script_path)}}}
        try:
            exec(code, globs)
        except Exception as e:
            print(f"SANDBOX EXCEPTION: {{type(e).__name__}}")
            sys.exit(1)
    else:
        # Patching __main__ script
        with open({repr(original_script_path)}, 'r', encoding='utf-8') as script_file:
            source = script_file.read()
        code = compile(source, {repr(original_script_path)}, 'exec')

        new_consts = list(code.co_consts)
        for i, const in enumerate(new_consts):
            if type(const).__name__ == 'code' and const.co_name == {repr(func_name)}:
                new_consts[i] = new_func_code
                break
        code = code.replace(co_consts=tuple(new_consts))

        globs = {{'__name__': '__main__', '__file__': {repr(original_script_path)}}}
        try:
            exec(code, globs)
        except Exception as e:
            print(f"SANDBOX EXCEPTION: {{type(e).__name__}}")
            sys.exit(1)

if __name__ == '__main__':
    main()
"""
        f.write(runner_code)
        runner_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, runner_path],
            capture_output=True,
            text=True,
            timeout=3
        )
        output = result.stdout + result.stderr

        if f"SANDBOX EXCEPTION: {exc_type_name}" in output:
            print("[CodeSuture Sandbox] Fix FAILED: original exception still occurs.")
            return False
        elif "SANDBOX EXCEPTION" in output:
            print(f"[CodeSuture Sandbox] Fix FAILED: caused a new exception.\n{output}")
            return False
        elif result.returncode != 0:
            print(f"[CodeSuture Sandbox] Fix FAILED: subprocess exited with error.\n{output}")
            return False

        print("[CodeSuture Sandbox] Fix PASSED!")
        return True
    except subprocess.TimeoutExpired:
        print("[CodeSuture Sandbox] Fix FAILED: Timeout (possible infinite loop).")
        return False
    finally:
        os.remove(source_path)
        os.remove(runner_path)