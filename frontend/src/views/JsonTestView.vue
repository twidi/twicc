<script setup>
// JsonTestView.vue - Temporary test page for JsonHumanView component.
// Access at /json-test

import JsonHumanView from '../components/JsonHumanView.vue'

const testCases = [
    {
        title: 'Bash tool (with override: command → string-code)',
        json: {
            command: 'git log --oneline -10 && echo "done"',
            description: 'Show recent git commits',
            timeout: 5000
        },
        overrides: { command: { valueType: 'string-code' } }
    },
    {
        title: 'Write tool (with override: content → string-code)',
        json: {
            file_path: '/home/user/project/src/utils/helpers.py',
            content: 'import os\nimport sys\nfrom pathlib import Path\n\n\ndef find_project_root(start_dir: str) -> Path:\n    """Walk up the directory tree to find the project root."""\n    current = Path(start_dir).resolve()\n    while current != current.parent:\n        if (current / "pyproject.toml").exists():\n            return current\n        current = current.parent\n    raise FileNotFoundError("No project root found")\n'
        },
        overrides: { content: { valueType: 'string-code', language: 'python' } }
    },
    {
        title: 'Edit tool (auto-generates diff from old_string / new_string)',
        json: {
            file_path: '/home/user/project/src/main.py',
            old_string: 'def process(data):\n    return data',
            new_string: 'def process(data):\n    if not data:\n        raise ValueError("Empty data")\n    return transform(data)'
        },
        overrides: {
            old_string: { valueType: 'string-code', language: 'python' },
            new_string: { valueType: 'string-code', language: 'python' }
        }
    },
    {
        title: 'Grep tool',
        json: {
            pattern: 'import\\s+asyncio',
            path: '/home/user/project/src',
            type: 'py',
            output_mode: 'content',
            context: 2
        },
    },
    {
        title: 'WebFetch tool',
        json: {
            url: 'https://docs.python.org/3/library/asyncio.html',
            prompt: 'Extract the main concepts and API overview for asyncio'
        },
    },
    {
        title: 'WebSearch tool',
        json: {
            query: 'Vue 3 recursive components best practices 2026',
            allowed_domains: ['vuejs.org', 'stackoverflow.com'],
            blocked_domains: ['w3schools.com']
        }
    },
    {
        title: 'AskUserQuestion tool (what the user sees in ask_user_question mode)',
        json: {
            questions: [
                {
                    question: 'Which database should we use for the new service?',
                    header: 'Database',
                    multiSelect: false,
                    options: [
                        { label: 'PostgreSQL', description: 'Reliable, feature-rich relational database' },
                        { label: 'SQLite', description: 'Lightweight, file-based, good for prototyping' },
                        { label: 'MongoDB', description: 'Document-oriented NoSQL database' }
                    ]
                }
            ]
        }
    },
    {
        title: 'TodoWrite tool',
        json: {
            todos: [
                { content: 'Implement user authentication', status: 'completed', activeForm: 'Implementing user authentication' },
                { content: 'Add input validation', status: 'in_progress', activeForm: 'Adding input validation' },
                { content: 'Write unit tests', status: 'pending', activeForm: 'Writing unit tests' }
            ]
        }
    },
    {
        title: 'NotebookEdit tool',
        json: {
            notebook_path: '/home/user/notebooks/analysis.ipynb',
            new_source: 'import pandas as pd\nimport matplotlib.pyplot as plt\n\ndf = pd.read_csv("data.csv")\ndf.describe()',
            cell_type: 'code',
            edit_mode: 'replace'
        },
        overrides: { new_source: { valueType: 'string-code', language: 'python' } }
    },
    {
        title: 'Read tool (simple)',
        json: {
            file_path: '/home/user/project/README.md',
            offset: 0,
            limit: 100
        },
    },
    {
        title: 'Glob tool',
        json: {
            pattern: '**/*.{ts,tsx}',
            path: '/home/user/project/src'
        }
    },
    {
        title: 'Unknown MCP tool with nested objects',
        json: {
            query: 'SELECT u.name, u.email FROM users u JOIN orders o ON u.id = o.user_id WHERE o.total > 100',
            database: 'production',
            options: {
                timeout: 5000,
                format: 'json',
                pagination: {
                    page: 1,
                    per_page: 50
                }
            },
            dry_run: true
        }
    },
    {
        title: 'Scalars and edge cases',
        json: {
            a_null_value: null,
            a_boolean_true: true,
            a_boolean_false: false,
            a_number_int: 42,
            a_number_float: 3.14159,
            an_empty_string: '',
            an_empty_object: {},
            an_empty_array: [],
            a_scalar_array: ['alpha', 'bravo', 'charlie', 'delta'],
            a_number_array: [1, 2, 3, 4, 5]
        }
    },
    {
        title: 'String with markdown content',
        json: {
            description: '## Overview\n\nThis module provides **async utilities** for the project.\n\n### Features\n\n- Connection pooling with `asyncio`\n- Automatic retry on failure\n- Configurable timeouts\n\n### Usage\n\n```python\nfrom utils import connect\n\nasync def main():\n    db = await connect("postgres://localhost/mydb")\n    result = await db.query("SELECT 1")\n```\n\n> Note: Requires Python 3.11+\n\n| Feature | Status |\n|---------|--------|\n| Pooling | Done |\n| Retry   | WIP  |\n| Timeout | Done |\n',
            version: '1.2.0'
        }
    },
    {
        title: 'Plain multi-line string (NOT markdown)',
        json: {
            stack_trace: 'Traceback (most recent call last):\n  File "/app/main.py", line 42, in handle_request\n    result = await process(data)\n  File "/app/process.py", line 15, in process\n    return transform(data)\n  File "/app/transform.py", line 8, in transform\n    raise ValueError("Invalid input format")\nValueError: Invalid input format',
            exit_code: 1
        }
    },
    {
        title: 'Long single-line string (should wrap naturally)',
        json: {
            prompt: 'You are a helpful assistant that analyzes code repositories. Given a repository URL, clone it, analyze the structure, identify the main programming languages used, count the lines of code per language, and provide a summary of the architecture including entry points, key modules, and dependency relationships between components.',
            temperature: 0.7
        }
    }
]
</script>

<template>
    <div class="json-test-page">
        <h1>JsonHumanView Test Page</h1>
        <p class="subtitle">Rendering various JSON structures with the JsonHumanView component</p>

        <div
            v-for="(testCase, index) in testCases"
            :key="index"
            class="test-case"
        >
            <h2 class="test-title">{{ testCase.title }}</h2>
            <div class="test-render">
                <JsonHumanView :value="testCase.json" :overrides="testCase.overrides ?? {}" />
            </div>
            <details class="test-raw">
                <summary>Raw JSON</summary>
                <pre>{{ JSON.stringify(testCase.json, null, 2) }}</pre>
            </details>
        </div>
    </div>
</template>

<style scoped>
.json-test-page {
    max-width: 900px;
    margin: 0 auto;
    padding: 2rem;
    color: var(--wa-color-text);
    height: 100vh;
    overflow-y: auto;
}

h1 {
    font-size: 1.5rem;
    margin-bottom: 0.25rem;
}

.subtitle {
    color: var(--wa-color-text-quiet);
    margin-bottom: 2rem;
}

.test-case {
    margin-bottom: 2rem;
    border: 1px solid var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-m);
    overflow: hidden;
}

.test-title {
    font-size: 0.9rem;
    font-weight: 600;
    padding: 0.75rem 1rem;
    background: var(--wa-color-neutral-5);
    border-bottom: 1px solid var(--wa-color-surface-border);
    margin: 0;
}

.test-render {
    padding: 1rem;
}

.test-raw {
    border-top: 1px solid var(--wa-color-surface-border);
    font-size: 0.8rem;
}

.test-raw summary {
    padding: 0.5rem 1rem;
    cursor: pointer;
    color: var(--wa-color-text-quiet);
}

.test-raw pre {
    padding: 0.5rem 1rem 1rem;
    margin: 0;
    font-size: 0.75rem;
    white-space: pre-wrap;
    word-break: break-word;
    background: var(--wa-color-neutral-5);
}
</style>
