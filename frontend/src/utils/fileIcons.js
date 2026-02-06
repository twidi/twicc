/**
 * File icon mapping utility.
 *
 * Maps file names, extensions, and folder names to vscode-icons identifiers
 * served via the Iconify CDN. Icons are fetched on demand as SVGs and cached
 * by the browser (Cache-Control: immutable, max-age=7 days).
 *
 * CDN URL format: https://api.iconify.design/vscode-icons/{icon-name}.svg
 */

const ICONIFY_BASE = 'https://api.iconify.design/vscode-icons'

// ─── Exact filename → icon identifier ────────────────────────────────────────
// Checked first (highest priority). Matches the full filename (case-insensitive).

const FILENAME_MAP = {
    // Docker
    'dockerfile': 'file-type-docker',
    'docker-compose.yml': 'file-type-docker',
    'docker-compose.yaml': 'file-type-docker',
    '.dockerignore': 'file-type-docker',
    // Git
    '.gitignore': 'file-type-git',
    '.gitattributes': 'file-type-git',
    '.gitmodules': 'file-type-git',
    // Editor / tooling config
    '.editorconfig': 'file-type-editorconfig',
    '.prettierrc': 'file-type-prettier',
    '.prettierrc.js': 'file-type-prettier',
    '.prettierrc.json': 'file-type-prettier',
    '.prettierrc.yaml': 'file-type-prettier',
    '.prettierrc.yml': 'file-type-prettier',
    '.prettierignore': 'file-type-prettier',
    '.eslintrc': 'file-type-eslint',
    '.eslintrc.js': 'file-type-eslint',
    '.eslintrc.json': 'file-type-eslint',
    '.eslintrc.cjs': 'file-type-eslint',
    'eslint.config.js': 'file-type-eslint',
    'eslint.config.mjs': 'file-type-eslint',
    'eslint.config.ts': 'file-type-eslint',
    '.babelrc': 'file-type-babel',
    'babel.config.js': 'file-type-babel',
    'babel.config.json': 'file-type-babel',
    // TypeScript config
    'tsconfig.json': 'file-type-tsconfig',
    'tsconfig.node.json': 'file-type-tsconfig',
    'tsconfig.app.json': 'file-type-tsconfig',
    // Build tools
    'vite.config.js': 'file-type-vite',
    'vite.config.ts': 'file-type-vite',
    'vite.config.mjs': 'file-type-vite',
    'vitest.config.js': 'file-type-vitest',
    'vitest.config.ts': 'file-type-vitest',
    'webpack.config.js': 'file-type-webpack',
    'webpack.config.ts': 'file-type-webpack',
    'rollup.config.js': 'file-type-rollup',
    'rollup.config.ts': 'file-type-rollup',
    'rollup.config.mjs': 'file-type-rollup',
    // Framework config
    'nuxt.config.ts': 'file-type-nuxt',
    'nuxt.config.js': 'file-type-nuxt',
    'next.config.js': 'file-type-next',
    'next.config.ts': 'file-type-next',
    'next.config.mjs': 'file-type-next',
    'astro.config.mjs': 'file-type-astro',
    'astro.config.ts': 'file-type-astro',
    'svelte.config.js': 'file-type-svelte',
    'tailwind.config.js': 'file-type-tailwind',
    'tailwind.config.ts': 'file-type-tailwind',
    // Package managers
    'package.json': 'file-type-npm',
    'package-lock.json': 'file-type-npm',
    '.npmrc': 'file-type-npm',
    '.npmignore': 'file-type-npm',
    'yarn.lock': 'file-type-yarn',
    '.yarnrc': 'file-type-yarn',
    '.yarnrc.yml': 'file-type-yarn',
    'pnpm-lock.yaml': 'file-type-pnpm',
    'pnpm-workspace.yaml': 'file-type-pnpm',
    '.pnpmfile.cjs': 'file-type-pnpm',
    // Python
    'pyproject.toml': 'file-type-python',
    'setup.py': 'file-type-python',
    'setup.cfg': 'file-type-python',
    'pipfile': 'file-type-pip',
    'pipfile.lock': 'file-type-pip',
    'poetry.lock': 'file-type-poetry',
    'pytest.ini': 'file-type-pytest',
    'conftest.py': 'file-type-pytest',
    '.flake8': 'file-type-python',
    'requirements.txt': 'file-type-pip',
    // Rust
    'cargo.toml': 'file-type-cargo',
    'cargo.lock': 'file-type-cargo',
    // Build
    'makefile': 'file-type-makefile',
    'cmakelists.txt': 'file-type-cmake',
    // License
    'license': 'file-type-license',
    'license.md': 'file-type-license',
    'license.txt': 'file-type-license',
    'licence': 'file-type-license',
    'licence.md': 'file-type-license',
    'licence.txt': 'file-type-license',
    // Env
    '.env': 'file-type-dotenv',
    '.env.local': 'file-type-dotenv',
    '.env.development': 'file-type-dotenv',
    '.env.production': 'file-type-dotenv',
    '.env.test': 'file-type-dotenv',
    '.env.example': 'file-type-dotenv',
    // Nginx
    'nginx.conf': 'file-type-nginx',
    // Terraform
    'terraform.tfvars': 'file-type-terraform',
}

// ─── File extension → icon identifier ────────────────────────────────────────
// Checked second. Matches the file extension (without the leading dot).

const EXTENSION_MAP = {
    // JavaScript / TypeScript
    'js': 'file-type-js',
    'mjs': 'file-type-js',
    'cjs': 'file-type-js',
    'jsx': 'file-type-reactjs',
    'ts': 'file-type-typescript',
    'mts': 'file-type-typescript',
    'cts': 'file-type-typescript',
    'tsx': 'file-type-reactts',
    'd.ts': 'file-type-typescriptdef',
    // Vue / Svelte / Angular
    'vue': 'file-type-vue',
    'svelte': 'file-type-svelte',
    // Styles
    'css': 'file-type-css',
    'scss': 'file-type-scss',
    'sass': 'file-type-sass',
    'less': 'file-type-less',
    // Markup
    'html': 'file-type-html',
    'htm': 'file-type-html',
    'xml': 'file-type-xml',
    'svg': 'file-type-svg',
    // Data / Config
    'json': 'file-type-json',
    'yaml': 'file-type-yaml',
    'yml': 'file-type-yaml',
    'toml': 'file-type-toml',
    'ini': 'file-type-ini',
    'cfg': 'file-type-ini',
    'conf': 'file-type-ini',
    // Documentation
    'md': 'file-type-markdown',
    'mdx': 'file-type-markdown',
    'txt': 'file-type-text',
    'log': 'file-type-log',
    'rst': 'file-type-text',
    // Python
    'py': 'file-type-python',
    'pyi': 'file-type-python',
    'pyx': 'file-type-python',
    'pyw': 'file-type-python',
    'ipynb': 'file-type-python',
    // Rust
    'rs': 'file-type-rust',
    // Go
    'go': 'file-type-go',
    // Java / Kotlin
    'java': 'file-type-java',
    'kt': 'file-type-kotlin',
    'kts': 'file-type-kotlin',
    // C / C++ / C# / Obj-C
    'c': 'file-type-c',
    'h': 'file-type-c',
    'cpp': 'file-type-cpp',
    'cc': 'file-type-cpp',
    'cxx': 'file-type-cpp',
    'hpp': 'file-type-cpp',
    'hh': 'file-type-cpp',
    'cs': 'file-type-csharp2',
    'm': 'file-type-objectivec',
    'mm': 'file-type-objectivec',
    // Swift
    'swift': 'file-type-swift',
    // Ruby
    'rb': 'file-type-ruby',
    'erb': 'file-type-ruby',
    'gemspec': 'file-type-ruby',
    // PHP
    'php': 'file-type-php',
    // Shell
    'sh': 'file-type-shell',
    'bash': 'file-type-shell',
    'zsh': 'file-type-shell',
    'fish': 'file-type-shell',
    // Lua
    'lua': 'file-type-lua',
    // R
    'r': 'file-type-r',
    // Perl
    'pl': 'file-type-perl',
    'pm': 'file-type-perl',
    // Scala
    'scala': 'file-type-scala',
    'sc': 'file-type-scala',
    // Haskell
    'hs': 'file-type-haskell',
    'lhs': 'file-type-haskell',
    // Elixir / Erlang
    'ex': 'file-type-elixir',
    'exs': 'file-type-elixir',
    'erl': 'file-type-erlang',
    'hrl': 'file-type-erlang',
    // Zig
    'zig': 'file-type-zig',
    // SQL
    'sql': 'file-type-sql',
    // GraphQL
    'graphql': 'file-type-graphql',
    'gql': 'file-type-graphql',
    // Protobuf
    'proto': 'file-type-protobuf',
    // Terraform
    'tf': 'file-type-terraform',
    'tfvars': 'file-type-terraform',
    // Images
    'png': 'file-type-image',
    'jpg': 'file-type-image',
    'jpeg': 'file-type-image',
    'gif': 'file-type-image',
    'webp': 'file-type-image',
    'ico': 'file-type-image',
    'bmp': 'file-type-image',
    // Binary / WebAssembly
    'wasm': 'file-type-wasm',
    'so': 'file-type-binary',
    'dll': 'file-type-binary',
    'exe': 'file-type-binary',
    'bin': 'file-type-binary',
    // Assembly
    'asm': 'file-type-assembly',
    's': 'file-type-assembly',
}

// ─── Folder name → icon identifier ───────────────────────────────────────────
// Maps directory names (case-insensitive) to a pair [closed, opened] identifiers.

const FOLDER_MAP = {
    // Source
    'src': ['folder-type-src', 'folder-type-src-opened'],
    'source': ['folder-type-src', 'folder-type-src-opened'],
    'lib': ['folder-type-src', 'folder-type-src-opened'],
    // Components
    'components': ['folder-type-component', 'folder-type-component-opened'],
    'component': ['folder-type-component', 'folder-type-component-opened'],
    // Views
    'views': ['folder-type-view', 'folder-type-view-opened'],
    'view': ['folder-type-view', 'folder-type-view-opened'],
    'pages': ['folder-type-view', 'folder-type-view-opened'],
    // Templates
    'templates': ['folder-type-template', 'folder-type-template-opened'],
    'template': ['folder-type-template', 'folder-type-template-opened'],
    // Styles
    'styles': ['folder-type-style', 'folder-type-style-opened'],
    'style': ['folder-type-style', 'folder-type-style-opened'],
    'css': ['folder-type-style', 'folder-type-style-opened'],
    'scss': ['folder-type-style', 'folder-type-style-opened'],
    // API
    'api': ['folder-type-api', 'folder-type-api-opened'],
    'apis': ['folder-type-api', 'folder-type-api-opened'],
    'endpoints': ['folder-type-api', 'folder-type-api-opened'],
    // Models
    'models': ['folder-type-model', 'folder-type-model-opened'],
    'model': ['folder-type-model', 'folder-type-model-opened'],
    // Controllers
    'controllers': ['folder-type-controller', 'folder-type-controller-opened'],
    'controller': ['folder-type-controller', 'folder-type-controller-opened'],
    // Middleware
    'middleware': ['folder-type-middleware', 'folder-type-middleware-opened'],
    'middlewares': ['folder-type-middleware', 'folder-type-middleware-opened'],
    // Hooks / Composables
    'hooks': ['folder-type-hook', 'folder-type-hook-opened'],
    'hook': ['folder-type-hook', 'folder-type-hook-opened'],
    'composables': ['folder-type-hook', 'folder-type-hook-opened'],
    // Plugins
    'plugins': ['folder-type-plugin', 'folder-type-plugin-opened'],
    'plugin': ['folder-type-plugin', 'folder-type-plugin-opened'],
    // Assets
    'assets': ['folder-type-asset', 'folder-type-asset-opened'],
    'asset': ['folder-type-asset', 'folder-type-asset-opened'],
    'static': ['folder-type-asset', 'folder-type-asset-opened'],
    // Public
    'public': ['folder-type-public', 'folder-type-public-opened'],
    // Images
    'images': ['folder-type-images', 'folder-type-images-opened'],
    'image': ['folder-type-images', 'folder-type-images-opened'],
    'img': ['folder-type-images', 'folder-type-images-opened'],
    'icons': ['folder-type-images', 'folder-type-images-opened'],
    // Tests
    'test': ['folder-type-test', 'folder-type-test-opened'],
    'tests': ['folder-type-test', 'folder-type-test-opened'],
    '__tests__': ['folder-type-test', 'folder-type-test-opened'],
    'spec': ['folder-type-test', 'folder-type-test-opened'],
    'specs': ['folder-type-test', 'folder-type-test-opened'],
    // Docs
    'docs': ['folder-type-docs', 'folder-type-docs-opened'],
    'doc': ['folder-type-docs', 'folder-type-docs-opened'],
    'documentation': ['folder-type-docs', 'folder-type-docs-opened'],
    // Config
    'config': ['folder-type-config', 'folder-type-config-opened'],
    'configs': ['folder-type-config', 'folder-type-config-opened'],
    'configuration': ['folder-type-config', 'folder-type-config-opened'],
    '.config': ['folder-type-config', 'folder-type-config-opened'],
    // Scripts
    'scripts': ['folder-type-script', 'folder-type-script-opened'],
    'script': ['folder-type-script', 'folder-type-script-opened'],
    'bin': ['folder-type-script', 'folder-type-script-opened'],
    // Server
    'server': ['folder-type-server', 'folder-type-server-opened'],
    'backend': ['folder-type-server', 'folder-type-server-opened'],
    // Dist / Build
    'dist': ['folder-type-dist', 'folder-type-dist-opened'],
    'build': ['folder-type-dist', 'folder-type-dist-opened'],
    'out': ['folder-type-dist', 'folder-type-dist-opened'],
    'output': ['folder-type-dist', 'folder-type-dist-opened'],
    // Node
    'node_modules': ['folder-type-node', 'folder-type-node-opened'],
    // Locales
    'locale': ['folder-type-locale', 'folder-type-locale-opened'],
    'locales': ['folder-type-locale', 'folder-type-locale-opened'],
    'i18n': ['folder-type-locale', 'folder-type-locale-opened'],
    // Mocks
    'mock': ['folder-type-mock', 'folder-type-mock-opened'],
    'mocks': ['folder-type-mock', 'folder-type-mock-opened'],
    '__mocks__': ['folder-type-mock', 'folder-type-mock-opened'],
    'fixtures': ['folder-type-mock', 'folder-type-mock-opened'],
    // Logs
    'logs': ['folder-type-log', 'folder-type-log-opened'],
    'log': ['folder-type-log', 'folder-type-log-opened'],
    // GitHub / VSCode
    '.github': ['folder-type-github', 'folder-type-github-opened'],
    '.vscode': ['folder-type-vscode', 'folder-type-vscode-opened'],
}


/**
 * Get the Iconify CDN URL for a vscode-icons icon.
 *
 * @param {string} iconId - The vscode-icons icon identifier (e.g. "file-type-python")
 * @returns {string} The full CDN URL to the SVG
 */
export function getIconUrl(iconId) {
    return `${ICONIFY_BASE}/${iconId}.svg`
}

/**
 * Get the icon identifier for a file based on its name.
 *
 * Resolution order:
 * 1. Exact filename match (case-insensitive)
 * 2. Compound extension match (e.g. ".d.ts")
 * 3. Simple extension match (e.g. ".py")
 * 4. Falls back to "default-file"
 *
 * @param {string} filename - The file name (e.g. "main.py", "Dockerfile")
 * @returns {string} The vscode-icons icon identifier
 */
export function getFileIconId(filename) {
    const lower = filename.toLowerCase()

    // 1. Exact filename match
    if (FILENAME_MAP[lower]) {
        return FILENAME_MAP[lower]
    }

    // 2. Compound extension (e.g. "foo.d.ts" → "d.ts")
    const parts = lower.split('.')
    if (parts.length > 2) {
        const compoundExt = parts.slice(-2).join('.')
        if (EXTENSION_MAP[compoundExt]) {
            return EXTENSION_MAP[compoundExt]
        }
    }

    // 3. Simple extension
    if (parts.length > 1) {
        const ext = parts[parts.length - 1]
        if (EXTENSION_MAP[ext]) {
            return EXTENSION_MAP[ext]
        }
    }

    // 4. Default
    return 'default-file'
}

/**
 * Get the icon identifier for a folder based on its name.
 *
 * @param {string} folderName - The folder name (e.g. "src", "node_modules")
 * @param {boolean} [opened=true] - Whether the folder is in its opened state
 * @returns {string} The vscode-icons icon identifier
 */
export function getFolderIconId(folderName, opened = true) {
    const lower = folderName.toLowerCase()
    const match = FOLDER_MAP[lower]
    if (match) {
        return opened ? match[1] : match[0]
    }
    return opened ? 'default-folder-opened' : 'default-folder'
}
