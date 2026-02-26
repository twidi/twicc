/**
 * Language detection from file paths.
 *
 * Maps file extensions to shiki language identifiers for syntax highlighting.
 * Used by JsonHumanView to detect the language of code content when a sibling
 * key contains a file path, and by PendingRequestForm for tool-specific overrides.
 *
 * Shiki has a fallback to 'text' for unknown languages, so it's safe to pass
 * any string — only known languages get syntax highlighting.
 */

// ─── File extension → shiki language identifier ─────────────────────────────
// Keys are lowercase extensions without the leading dot.

const EXTENSION_TO_LANGUAGE = {
    // JavaScript / TypeScript
    'js': 'javascript',
    'mjs': 'javascript',
    'cjs': 'javascript',
    'jsx': 'jsx',
    'ts': 'typescript',
    'mts': 'typescript',
    'cts': 'typescript',
    'tsx': 'tsx',
    'd.ts': 'typescript',
    // Vue / Svelte
    'vue': 'vue',
    'svelte': 'svelte',
    // Styles
    'css': 'css',
    'scss': 'scss',
    'sass': 'sass',
    'less': 'less',
    // Markup
    'html': 'html',
    'htm': 'html',
    'xml': 'xml',
    'svg': 'xml',
    // Data / Config
    'json': 'json',
    'jsonc': 'jsonc',
    'json5': 'json5',
    'yaml': 'yaml',
    'yml': 'yaml',
    'toml': 'toml',
    'ini': 'ini',
    'cfg': 'ini',
    // Documentation
    'md': 'markdown',
    'mdx': 'mdx',
    // Python
    'py': 'python',
    'pyi': 'python',
    'pyx': 'python',
    'pyw': 'python',
    // Rust
    'rs': 'rust',
    // Go
    'go': 'go',
    // Java / Kotlin
    'java': 'java',
    'kt': 'kotlin',
    'kts': 'kotlin',
    // C / C++ / C# / Obj-C
    'c': 'c',
    'h': 'c',
    'cpp': 'cpp',
    'cc': 'cpp',
    'cxx': 'cpp',
    'hpp': 'cpp',
    'hh': 'cpp',
    'cs': 'csharp',
    'm': 'objective-c',
    'mm': 'objective-cpp',
    // Swift
    'swift': 'swift',
    // Ruby
    'rb': 'ruby',
    'erb': 'erb',
    'gemspec': 'ruby',
    // PHP
    'php': 'php',
    // Shell
    'sh': 'bash',
    'bash': 'bash',
    'zsh': 'zsh',
    'fish': 'fish',
    // Lua
    'lua': 'lua',
    // R
    'r': 'r',
    // Perl
    'pl': 'perl',
    'pm': 'perl',
    // Scala
    'scala': 'scala',
    'sc': 'scala',
    // Haskell
    'hs': 'haskell',
    'lhs': 'haskell',
    // Elixir / Erlang
    'ex': 'elixir',
    'exs': 'elixir',
    'erl': 'erlang',
    'hrl': 'erlang',
    // Zig
    'zig': 'zig',
    // SQL
    'sql': 'sql',
    // GraphQL
    'graphql': 'graphql',
    'gql': 'graphql',
    // Protobuf
    'proto': 'proto',
    // Terraform
    'tf': 'terraform',
    'tfvars': 'terraform',
    // Docker
    'dockerfile': 'dockerfile',
    // Makefile
    'makefile': 'makefile',
    // Nix
    'nix': 'nix',
    // Dart
    'dart': 'dart',
    // Clojure
    'clj': 'clojure',
    'cljs': 'clojure',
    'cljc': 'clojure',
    // Assembly
    'asm': 'asm',
    's': 'asm',
}

// ─── Exact filename → shiki language identifier ─────────────────────────────
// Checked first (highest priority). Matches the full filename (case-insensitive).

const FILENAME_TO_LANGUAGE = {
    'dockerfile': 'dockerfile',
    'makefile': 'makefile',
    'cmakelists.txt': 'cmake',
    'gemfile': 'ruby',
    'rakefile': 'ruby',
    '.gitignore': 'gitignore',
    '.gitattributes': 'gitattributes',
    '.env': 'dotenv',
    '.env.local': 'dotenv',
    '.env.development': 'dotenv',
    '.env.production': 'dotenv',
    '.env.test': 'dotenv',
    '.env.example': 'dotenv',
}

/**
 * Detect the shiki language identifier from a file path.
 *
 * Resolution order:
 * 1. Exact filename match (case-insensitive)
 * 2. Compound extension match (e.g. ".d.ts")
 * 3. Simple extension match (e.g. ".py")
 * 4. Returns null (caller should fall back to no language / plain text)
 *
 * @param {string} filePath - A file path or filename (e.g. "/src/main.py", "script.sh")
 * @returns {string|null} Shiki language identifier or null if unknown
 */
export function getLanguageFromPath(filePath) {
    if (!filePath || typeof filePath !== 'string') return null

    const filename = filePath.split('/').pop() || filePath
    const lower = filename.toLowerCase()

    // 1. Exact filename match
    if (FILENAME_TO_LANGUAGE[lower]) {
        return FILENAME_TO_LANGUAGE[lower]
    }

    const parts = lower.split('.')

    // 2. Compound extension (e.g. "foo.d.ts" → "d.ts")
    if (parts.length > 2) {
        const compoundExt = parts.slice(-2).join('.')
        if (EXTENSION_TO_LANGUAGE[compoundExt]) {
            return EXTENSION_TO_LANGUAGE[compoundExt]
        }
    }

    // 3. Simple extension
    if (parts.length > 1) {
        const ext = parts[parts.length - 1]
        if (EXTENSION_TO_LANGUAGE[ext]) {
            return EXTENSION_TO_LANGUAGE[ext]
        }
    }

    // 4. Unknown
    return null
}
