import os
import tokenize
import io
import re

def clean_file_aggressive(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        source = f.read()

    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except tokenize.TokenError:
        return False
    
    drop_indices = set()
    
    for i, tok in enumerate(tokens):
        if tok.type == tokenize.COMMENT:
            text = tok.string
            body = text.lstrip('#').strip()
            lower_body = body.lower()
            

            if text.startswith('#!') or text.startswith('# -*-'):
                continue
            if 'type: ignore' in text or 'fmt: off' in text or 'fmt: on' in text:
                continue
            if re.search(r'\b(TODO|FIXME|HACK|NOTE|WARNING|NOQA)\b', text, re.IGNORECASE):
                continue
            if 'license' in lower_body or 'copyright' in lower_body:
                continue
                

            line_tokens = [t for t in tokens if t.start[0] == tok.start[0]]
            is_after_pass = False
            for prev in line_tokens:
                if prev.type == tokenize.NAME and prev.string == 'pass':
                    is_after_pass = True
                    break
            if is_after_pass:
                continue
                

            keep_keywords = [
                'cpython', 'pep', 'architecture', 'copy.copy', 'sha-256', 
                'sys.monitoring', '3.11', '3.12', '3.13', 'exception table', 
                'thread', 'lock', 'race', 'memory', 'layout', 'bytecode', 'opcode', 
                'toctou', 'idempotent', 'rewind', 'despecialize', 
                'pyfunction_setcode', 'ctypes', 'security', 'corrupt', 
                'backward compat', 'race-condition', 'compatibility', 'trade-off',
                'algorithmic'
            ]
            
            if any(kw in lower_body for kw in keep_keywords):
                continue
                
            if re.search(r'\b(why|because|avoid|prevent|force|ensure|workaround|hack)\b', lower_body):
                continue
                

            remove_exact = [
                "get recent incidents", "apply patch", "replay request", "log incident",
                "json output", "human-readable output", "default: show all", "table output",
                "summary", "helpers", "recording", "querying", "public api",
                "instruction builders", "jump opcodes", "status metrics", "incident metrics",
                "keep last occurrence", "dict — use safe .get()", "not a dict — use direct subscript",
                "dict - use safe .get()", "not a dict - use direct subscript",
                "dict \u2014 use safe .get()", "not a dict \u2014 use direct subscript"
            ]
            if any(lower_body == r for r in remove_exact):
                drop_indices.add(i)
                continue
                

            if re.match(r'^[-=_\*~-]+$', body) or re.match(r'^[-=_\*~-]+\s*[-=_\*~-]+$', body) or re.match(r'^[-=_\*~-\s]+$', body):
                drop_indices.add(i)
                continue
                

            if re.match(r'^phase\s*\d+', lower_body) or lower_body.startswith('v2'):
                drop_indices.add(i)
                continue
                

            drop_indices.add(i)

    if not drop_indices:
        return False
        
    new_source_lines = source.splitlines(True)
    drops_by_line = {}
    for i in drop_indices:
        tok = tokens[i]
        lno = tok.start[0] - 1
        drops_by_line.setdefault(lno, []).append(tok)
        
    for lno, toks in drops_by_line.items():
        line = new_source_lines[lno]
        tok = toks[0]
        new_line = line[:tok.start[1]]
        if not new_line.strip():
            new_line = '\n' if line.endswith('\n') else ''
        else:
            new_line = new_line.rstrip() + ('\n' if line.endswith('\n') else '')
        new_source_lines[lno] = new_line
        
    new_source = "".join(new_source_lines)
    

    new_source = re.sub(r'\n{3,}', '\n\n', new_source)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_source)
        
    return True

skip_dirs = {'.git', '__pycache__', '.egg-info', 'dist', 'build', '.venv', 'venv'}

modified_count = 0
for root, dirs, files in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in skip_dirs and not d.endswith('.egg-info')]
    for file in files:
        if file.endswith('.py'):
            filepath = os.path.join(root, file)
            if clean_file_aggressive(filepath):
                print(f"Aggressively cleaned {filepath}")
                modified_count += 1
print(f"Total files aggressively cleaned: {modified_count}")
