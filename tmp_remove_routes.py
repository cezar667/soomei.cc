from pathlib import Path
path = Path('api/app.py')
lines = path.read_text(encoding='utf-8').splitlines(keepends=True)
start = None
end = None
for idx,line in enumerate(lines):
    if line.startswith('@app.get("/auth/forgot"'):
        start = idx
    if start is not None and line.startswith('@app.get("/auth/verify"'):
        end = idx
        break
if start is not None and end is not None:
    del lines[start:end]
path.write_text(''.join(lines), encoding='utf-8')
