import os
import tokenize
import io
import re

def is_keep_comment(text, is_inline):
    lower_text = text.lower()
    

    if text.startswith('#!') or text.startswith('# -*-'):
        return True
        

    if re.search(r'\b(TODO|FIXME|HACK|NOTE|WARNING|NOQA)\b', text, re.IGNORECASE):
        return True
    if 'type: ignore' in text or 'fmt: off' in text or 'fmt: on' in text:
        return True
    if 'license' in lower_text or 'copyright' in lower_text:
        return True

    whitelist = [
        'cpython', 'pep ', 'bytecode', 'opcode', 'exception table', 'frame', 
        'ctypes', 'memory', 'thread', 'race', 'toctou', 'lock', 'security', 
        'compat', 'trade-off', 'tradeoff', 'algorithm', 'hash', 'collision', 
        'because', 'due to', 'prevent', 'avoid', 'force', 'require', 
        'never crash', 'disconnected', 'optional', 'idempotent', 'mutate', 
        'replay', 'de-specialize', 'specializ', 'cache', 'corrupt', 'fallback', 
        'side effect', 'guarantee', 'unsafe', 'safe', 'assumption',
        'cost', 'expensive', 'cheap', 'o(', 'performance', 'bottleneck',
        'workaround', 'hack', 'edge case', 'corner case', 'deadlock', 'gil ',
        're-entrant', 'reentrant', 'atomic', 'volatile', 'pointer',
        'sys.monitoring', 'sys.settrace'
    ]
    
    if any(w in lower_text for w in whitelist):
        return True
        
    return False

def clean_file(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
        enc = 'utf-8'
    except UnicodeDecodeError:
        with open(filepath, 'r', encoding='mbcs') as f:
            source = f.read()
        enc = 'mbcs'

    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except tokenize.TokenError:
        return False
    
    drop_indices = set()
    
    for i, tok in enumerate(tokens):
        if tok.type == tokenize.COMMENT:
            text = tok.string
            body = text.lstrip('#').strip()
            

            is_inline = False
            if i > 0 and tokens[i-1].start[0] == tok.start[0] and tokens[i-1].type != tokenize.NL and tokens[i-1].type != tokenize.NEWLINE:
                is_inline = True
                
            if not is_keep_comment(text, is_inline):
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
    

    if new_source.startswith('\n\n'):
        new_source = new_source.lstrip('\n') + '\n'
        
    with open(filepath, 'w', encoding=enc) as f:
        f.write(new_source)
        
    return True

skip_dirs = {'.git', '__pycache__', '.egg-info', 'dist', 'build', '.venv', 'venv'}

modified_count = 0
for root, dirs, files in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in skip_dirs and not d.endswith('.egg-info')]
    for file in files:
        if file.endswith('.py'):
            filepath = os.path.join(root, file)
            if clean_file(filepath):
                print(f"Cleaned {filepath}")
                modified_count += 1
print(f"Total files aggressively cleaned: {modified_count}")
