import os
import marshal
import hashlib
import sys
import importlib.abc
import inspect
import json
from datetime import datetime, timezone
import threading

CACHE_DIR = ".codesuture_store"
HEALED_FUNCTIONS = set()
ANNOUNCED_HEALED_FUNCTIONS = set()
_store_lock = threading.Lock()

def _heal_key(module_name, func_name, key_name=None):

    base = f"{module_name}.{func_name}"
    if key_name:
        return f"{base}:{key_name}"
    return base

def save_patch(func, new_code, spec=None, ttl_days=7, original_code=None):
    module_name = getattr(func, '__module__', None)
    if not module_name:
        return
    func_name = getattr(func, '__qualname__', func.__name__)
    func_name = func_name.replace('<', '_').replace('>', '_')

    os.makedirs(CACHE_DIR, exist_ok=True)

    base_name = f"{module_name}.{func_name}"
    cache_path = os.path.join(CACHE_DIR, f"{base_name}.code")
    json_path = os.path.join(CACHE_DIR, f"{base_name}.json")
    orig_path = os.path.join(CACHE_DIR, f"{base_name}.orig.code")

    if original_code is not None:
        with _store_lock:
            with open(orig_path, "wb") as f:
                marshal.dump(original_code, f)

    code_bytes = marshal.dumps(new_code)
    code_hash = hashlib.sha256(code_bytes).hexdigest()
    with _store_lock:
        with open(cache_path, "wb") as f:
            f.write(code_bytes)

    if spec is not None:
        target_name = spec.var_name
        if spec.strategy == 'null_guard' and spec.key_name:
            target_name = spec.key_name[-1] if isinstance(spec.key_name, tuple) else spec.key_name

        metadata = {
            "func_name": func_name,
            "guard_type": spec.strategy,
            "target": target_name,
            "default_value": spec.default_value,
            "patched_at": datetime.now(timezone.utc).isoformat(),
            "ttl_days": ttl_days,
            "code_sha256": code_hash
        }
        metadata["thread"] = threading.current_thread().name
        with _store_lock:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)

def _announce_healed(module_name, func_name, key_name=None):
    key = _heal_key(module_name, func_name, key_name)
    with _store_lock:
        if key in ANNOUNCED_HEALED_FUNCTIONS:
            return
        ANNOUNCED_HEALED_FUNCTIONS.add(key)
    if key_name:
        print(f"[CodeSuture] Already healed, skipping: loaded persistent patch for {module_name}.{func_name} ({key_name})")
    else:
        print(f"[CodeSuture] Already healed, skipping: loaded persistent patch for {module_name}.{func_name}")

def _load_cached_code(module_name, func_name):
    func_name = func_name.replace('<', '_').replace('>', '_')
    if not os.path.isdir(CACHE_DIR):
        return None

    base_name = f"{module_name}.{func_name}"
    code_path = os.path.join(CACHE_DIR, f"{base_name}.code")
    json_path = os.path.join(CACHE_DIR, f"{base_name}.json")
    if not os.path.isfile(code_path):
        return None

    with open(code_path, "rb") as f:
        code_bytes = f.read()

    file_hash = hashlib.sha256(code_bytes).hexdigest()
    stored_hash = None

    if os.path.isfile(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                patch_data = json.load(f)
            stored_hash = patch_data.get("code_sha256")
            patched_at = datetime.fromisoformat(patch_data["patched_at"])
            if patched_at.tzinfo is None:
                patched_at = patched_at.replace(tzinfo=timezone.utc)
            ttl_days = patch_data.get("ttl_days", 7)
            age_days = (datetime.now(timezone.utc) - patched_at).days
            if age_days > ttl_days:
                print(
                    f"[CodeSuture] [WARN]  Patch for '{patch_data.get('func_name', func_name)}' "
                    f"is {age_days} day(s) old (TTL={ttl_days}d). "
                    f"Verify root cause is fixed in source. "
                    f"Run 'codesuture audit' to review all patches."
                )
        except Exception:
            pass

    if stored_hash is not None:
        if file_hash != stored_hash:
            print(f"[CodeSuture] WARNING: Patch integrity check failed for {func_name} \u2014 refusing to load")
            return None
    else:
        print(f"[CodeSuture] WARNING: Legacy patch without integrity check for {func_name}")

    with _store_lock:
        return marshal.loads(code_bytes)

def _load_learned_code(func, func_name):
    from codesuture.knowledge import load_learned_rules

    for rule in load_learned_rules():
        if rule["func_name"] != func_name:
            continue

        from codesuture.guard_synthesizer import synthesize_guarded_code
        from codesuture.pattern_matcher import PatchSpec

        spec = PatchSpec(
            strategy='autonomous_rule',
            var_name=func_name,
            default_value=rule["new_source"],
        )
        new_bc = synthesize_guarded_code(func.__code__, spec)
        return new_bc.to_code()
    return None

def apply_persisted_patch_to_function(func, module_name=None, func_name=None, key_name=None, announce=True):

    module_name = module_name or getattr(func, '__module__', None)
    func_name = func_name or getattr(func, '__qualname__', getattr(func, '__name__', None))
    if not module_name or not func_name:
        return False

    new_code = _load_cached_code(module_name, func_name)
    if new_code is None:
        try:
            new_code = _load_learned_code(func, func_name)
        except Exception:
            new_code = None
    if new_code is None:
        return False

    from codesuture.code_replacer import replace_function_code

    replace_function_code(func, new_code)
    with _store_lock:
        HEALED_FUNCTIONS.add(_heal_key(module_name, func_name, key_name))
    if announce:
        _announce_healed(module_name, func_name, key_name)
    return True

def _iter_cached_function_names(module_name):
    if not os.path.isdir(CACHE_DIR):
        return
    prefix = f"{module_name}."
    for filename in os.listdir(CACHE_DIR):
        if filename.startswith(prefix) and filename.endswith(".code") and not filename.endswith(".orig.code"):
            yield filename[len(prefix):-5]

def _resolve_attr(root, dotted_name):
    obj = root
    for part in dotted_name.split('.'):
        obj = getattr(obj, part)
    return obj

def apply_persisted_patch_to_value(module_name, binding_name, value):
    if hasattr(value, '__wrapped__'):
        value = getattr(value, '__wrapped__')
    if inspect.isfunction(value):
        return apply_persisted_patch_to_function(value, module_name=module_name)

    applied = False
    if inspect.isclass(value):
        prefix = f"{binding_name}."
        for func_name in _iter_cached_function_names(module_name) or ():
            if not func_name.startswith(prefix):
                continue
            try:
                target = _resolve_attr(value, func_name[len(prefix):])
                if isinstance(target, property) and target.fget is not None:
                    target = target.fget
                if inspect.ismethod(target):
                    target = target.__func__
                if hasattr(target, '__wrapped__'):
                    target = getattr(target, '__wrapped__')
                if inspect.isfunction(target):
                    applied = apply_persisted_patch_to_function(
                        target,
                        module_name=module_name,
                        func_name=func_name,
                    ) or applied
            except Exception:
                continue
    return applied

class CodeSutureGlobals(dict):
    def __init__(self, module_name, initial=None):
        super().__init__()
        self._codesuture_module_name = module_name
        if initial:
            for key, value in initial.items():
                dict.__setitem__(self, key, value)

    def __setitem__(self, key, value):
        dict.__setitem__(self, key, value)
        tracer = sys.gettrace()
        sys.settrace(None)
        try:
            apply_persisted_patch_to_value(self._codesuture_module_name, key, value)
        except Exception:
            pass
        finally:
            sys.settrace(tracer)

def make_persisted_patch_globals(module_name, initial=None):
    return CodeSutureGlobals(module_name, initial)

def apply_persisted_patches(module):
    module_name = getattr(module, '__name__', None)
    if not module_name:
        return

    for func_name in _iter_cached_function_names(module_name) or ():
        try:
            obj = _resolve_attr(module, func_name)
            if isinstance(obj, property) and obj.fget is not None:
                obj = obj.fget
            if inspect.ismethod(obj):
                obj = obj.__func__
            if hasattr(obj, '__wrapped__'):
                obj = getattr(obj, '__wrapped__')
            if inspect.isfunction(obj):
                apply_persisted_patch_to_function(obj, module_name, func_name)
        except Exception:
            pass

    from codesuture.knowledge import load_learned_rules
    rules = load_learned_rules()
    for rule in rules:
        func_name = rule["func_name"]

        parts = func_name.split('.')
        obj = module
        try:
            for part in parts:
                obj = getattr(obj, part)

            with _store_lock:
                if _heal_key(module_name, func_name) in HEALED_FUNCTIONS:
                    continue

            if isinstance(obj, property) and obj.fget is not None:
                obj = obj.fget
            if inspect.ismethod(obj):
                obj = obj.__func__
            if hasattr(obj, '__wrapped__'):
                obj = getattr(obj, '__wrapped__')
            if inspect.isfunction(obj):
                apply_persisted_patch_to_function(obj, module_name, func_name)
        except Exception:
            continue

class CodeSutureLoaderWrapper(importlib.abc.Loader):
    def __init__(self, loader):
        self.loader = loader

    def create_module(self, spec):
        if hasattr(self.loader, 'create_module'):
            return self.loader.create_module(spec)
        return None

    def exec_module(self, module):
        self.loader.exec_module(module)
        apply_persisted_patches(module)

    def __getattr__(self, name):
        return getattr(self.loader, name)

class CodeSutureMetaFinder(importlib.abc.MetaPathFinder):
    def __init__(self):
        self._local = threading.local()

    def find_spec(self, fullname, path, target=None):
        if getattr(self._local, '_inside', False): return None
        self._local._inside = True
        try:
            for finder in sys.meta_path:
                if finder is self: continue
                if hasattr(finder, 'find_spec'):
                    spec = finder.find_spec(fullname, path, target)
                    if spec is not None and getattr(spec, 'loader', None) is not None:
                        if not isinstance(spec.loader, CodeSutureLoaderWrapper):
                            spec.loader = CodeSutureLoaderWrapper(spec.loader)
                        return spec
        finally:
            self._local._inside = False
        return None

def install_import_hook():
    if not any(isinstance(f, CodeSutureMetaFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, CodeSutureMetaFinder())

    for module_name, module in list(sys.modules.items()):
        if module is not None:
            apply_persisted_patches(module)

def patch_script_code(code_obj, module_name="__main__"):
    if not os.path.isdir(CACHE_DIR):
        return code_obj

    new_consts = list(code_obj.co_consts)
    changed = False
    for i, const in enumerate(new_consts):
        if type(const).__name__ == 'code':
            func_name = const.co_name
            base_name = f"{module_name}.{func_name}"
            code_path = os.path.join(CACHE_DIR, f"{base_name}.code")
            new_code = None
            if os.path.isfile(code_path):
                with open(code_path, "rb") as f:
                    new_code = marshal.load(f)
            else:

                from codesuture.knowledge import load_learned_rules
                rules = load_learned_rules()
                for rule in rules:
                    if rule["func_name"] == func_name:
                        from codesuture.guard_synthesizer import synthesize_guarded_code
                        from codesuture.pattern_matcher import PatchSpec
                        spec = PatchSpec(strategy='autonomous_rule', var_name=func_name, default_value=rule["new_source"])
                        try:
                            new_bc = synthesize_guarded_code(const, spec)
                            new_code = new_bc.to_code()
                        except Exception:
                            continue
                        break

            if new_code:
                new_consts[i] = new_code
                changed = True
                with _store_lock:
                    HEALED_FUNCTIONS.add(_heal_key(module_name, func_name))
                _announce_healed(module_name, func_name)
    if changed:
        return code_obj.replace(co_consts=tuple(new_consts))
    return code_obj