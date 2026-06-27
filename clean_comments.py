import os
import tokenize
import io
import re

def clean_file(filepath):
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
                

            if re.search(r'\b(TODO|FIXME|HACK|NOTE|WARNING|NOQA)\b', text, re.IGNORECASE):
                continue
            if 'type: ignore' in text or 'fmt: off' in text or 'fmt: on' in text:
                continue
            
            # Keep license headers (usually at the very top, containing 'license' or 'copyright')
            if 'license' in lower_body or 'copyright' in lower_body:
                continue
                
            # Keep architecture / CPython notes
            if 'cpython' in text or 'pep' in lower_body or 'architecture' in lower_body or 'copy.copy' in text or 'sha-256' in lower_body:
                continue
                
            is_bad = False
            

            if re.match(r'^[-=_\*~]+$', body) or re.match(r'^[-=_\*~]+\s+[-=_\*~]+$', body):
                is_bad = True
                

            forbidden = [
                "this function", "this method", "this class", "we can", "we will", 
                "we need to", "now we", "here we", "the following", "this is a", "this is the"
            ]
            if any(lower_body.startswith(f) for f in forbidden):
                is_bad = True
                

            obvious = [
                "initialize", "loop through", "return", "call", "set the", "set ",
                "check if", "create a new", "create new", "import", "get the", "parse",
                "update", "add the", "remove", "calculate", "print",
                "if ", "else", "try", "catch", "extract", "find ", "check for",
                "process", "store", "load", "save", "handle", "execute", "run ",
                "append", "format", "build", "construct", "convert", "yield",
                "generate", "wrap", "start", "stop", "read", "write", "close", "open"
            ]
            
            # Check if it starts with an obvious verb and doesn't contain "because", "why", "workaround"
            if not is_bad and any(lower_body.startswith(o) for o in obvious):
                if "because" not in lower_body and "why" not in lower_body and "workaround" not in lower_body:
                    is_bad = True
                    
            if is_bad:
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
            if clean_file(filepath):
                print(f"Cleaned {filepath}")
                modified_count += 1
print(f"Total files cleaned: {modified_count}")
