import os
files = [
    r'D:\DINNO\DEV\AI-AutoRouting\RoutingAI\src\BuildFeatureVectors.py',
    r'D:\DINNO\DEV\AI-AutoRouting\RoutingAI\src\BuildContextVectors.py',
    r'D:\DINNO\DEV\AI-AutoRouting\RoutingAI\src\BuildDesignGroups.py',
    r'D:\DINNO\DEV\AI-AutoRouting\RoutingAI\src\BuildSegmentTemplates.py'
]

for f in files:
    print(f'=== {os.path.basename(f)} ===')
    try:
        with open(f, 'r', encoding='utf-8') as file:
            content = file.read()
            lines = content.split('\n')
            funcs = [l for l in lines if l.strip().startswith('def ') or l.strip().startswith('class ')]
            print(f'Size: {len(lines)} lines')
            print('Functions/Classes:')
            for fn in funcs:
                print('  ' + fn.strip())
    except Exception as e:
        print('Error:', e)
    print('\n')
