import os
import ast
import json

base_dir = r'c:\Users\User\OneDrive\Desktop\selfmade game projects\llm trader - Checkpoint dicisipline soilder - used in antigravity'

files_to_check = {
    'DATA': [
        'data/market_data_provider.py',
        'data/data_quality.py',
        'data/data_requirements.py',
        'data/data_source_pipeline.py',
        'data/realtime_news_ingest.py',
        'data/llm_router.py',
        'data/cerebras_client.py'
    ],
    'RESEARCH': [
        'research/kronos_bridge.py',
        'research/kronos_mt5_adapter.py',
        'research/ppo_trainer.py',
        'research/meta_rl_trainer.py',
        'research/supervised_training.py',
        'research/contextual_bandit.py',
        'research/baseline_research.py',
        'research/research_validation.py',
        'research/model_registry.py'
    ],
    'OPERATIONS': [
        'operations/agent_operations.py',
        'operations/agent_contracts.py',
        'operations/journal_audit.py',
        'operations/journal_backup.py',
        'operations/daily_reconciliation.py',
        'operations/live_rollout_gate.py',
        'operations/readiness_report.py',
        'operations/security_audit.py'
    ]
}

print('=== START REPORT ===')

for category, files in files_to_check.items():
    print(f'\n# {category} FOLDER')
    total_lines = 0
    for f in files:
        full_path = os.path.join(base_dir, f)
        if not os.path.exists(full_path):
            print(f'File not found: {f}')
            continue
        
        with open(full_path, 'r', encoding='utf-8') as file:
            content = file.read()
            lines = content.splitlines()
            num_lines = len(lines)
            total_lines += num_lines
            
            try:
                tree = ast.parse(content)
                
                classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
                functions = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
                imports = [node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)]
                imports_from = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module]
                try_count = len([node for node in ast.walk(tree) if isinstance(node, ast.Try)])
                
                print(f'\n## File: {f}')
                print(f'- Line count: {num_lines}')
                print(f'- Approx complexity: {len(classes)} classes, {len(functions)} functions, {try_count} try-except blocks')
                print(f'- Key classes: {", ".join(classes[:5])}{"..." if len(classes)>5 else ""}')
                print(f'- Key functions: {", ".join(functions[:10])}{"..." if len(functions)>10 else ""}')
                print(f'- Error handling: {"Robust" if try_count > 3 else "Basic"} ({try_count} try blocks)')
                
                # Check for RL/ML keywords
                ml_keywords = ['keras', 'tensorflow', 'torch', 'rl', 'ppo', 'stable_baselines3', 'gym', 'env', 'reward', 'train']
                has_ml = any(kw in content.lower() for kw in ml_keywords)
                print(f'- ML/RL architecture hints: {"Yes" if has_ml else "No"}')
                
                # Data flow
                has_api = 'requests' in imports or 'aiohttp' in imports
                has_db = 'sqlite3' in imports or 'sqlalchemy' in imports or 'psycopg2' in imports
                print(f'- Data flow hints: API requests: {has_api}, Database: {has_db}')
                
            except SyntaxError:
                print(f'\n## File: {f}')
                print(f'- Line count: {num_lines}')
                print(f'- Syntax Error parsing AST')
    print(f'Total {category} Lines: {total_lines}')

print('\n# ROOT FOLDER')
req_path = os.path.join(base_dir, 'requirements.txt')
if os.path.exists(req_path):
    with open(req_path, 'r', encoding='utf-8') as file:
        lines = file.readlines()
        print(f'\n## File: requirements.txt')
        print(f'- Line count: {len(lines)}')
        print(f'- Content preview: {" ".join([l.strip() for l in lines[:10]])}')

strat_path = os.path.join(base_dir, 'strategy_registry.json')
if os.path.exists(strat_path):
    with open(strat_path, 'r', encoding='utf-8') as file:
        content = file.read()
        lines = content.splitlines()
        print(f'\n## File: strategy_registry.json')
        print(f'- Line count: {len(lines)}')
        try:
            data = json.loads(content)
            num_strats = len(data.get('strategies', []))
            print(f'- Content summary: Contains {num_strats} strategies')
        except json.JSONDecodeError:
            print(f'- Content summary: Invalid JSON')
print('=== END REPORT ===')
