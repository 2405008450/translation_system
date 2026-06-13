import zhCN from './zh-CN'

type LocaleNode = string | number | boolean | null | LocaleTree | LocaleNode[]
type LocaleTree = { [key: string]: LocaleNode }

const overrides = {
  common: {
    brand: 'Translation Workbench',
    comingSoon: 'Coming soon',
    notSet: 'Not set',
    none: 'None',
    empty: 'No data',
    loading: 'Loading...',
    progress: {
      total: 'Overall progress',
      workflowDetail: 'Workflow progress',
    },
    actions: {
      close: 'Close',
      cancel: 'Cancel',
      confirm: 'Confirm',
      create: 'Create',
      save: 'Save',
      saving: 'Saving...',
      delete: 'Delete',
      edit: 'Edit',
      refresh: 'Refresh',
      upload: 'Upload',
      import: 'Import',
      export: 'Export',
      back: 'Back',
      details: 'View details',
      stop: 'Stop',
      pause: 'Pause',
      reply: 'Reply',
    },
    roles: {
      superAdmin: 'Super admin',
      admin: 'Admin',
      user: 'User',
    },
  },
  pages: {
    dashboard: {
      title: 'Dashboard',
      description: 'View project, translation, LLM usage, and user activity trends',
    },
    projects: {
      title: 'Projects',
      description: 'View all translation projects and manage progress and assignments',
    },
    projectDetail: {
      title: 'Project Details',
      description: 'Upload source documents and review project processing progress',
    },
    tasks: {
      title: 'My Tasks',
      description: 'View assigned tasks and open the translation workbench',
    },
    workbench: {
      title: 'Translation Workbench',
      description: 'Edit segments, run AI revision, and export translated documents',
    },
    tm: {
      title: 'Translation Memory',
      description: 'Import terminology and bilingual segments to improve matching',
    },
    tmEdit: {
      title: 'TM Details',
      description: 'Maintain TM metadata, entries, imports, and exports',
    },
    termBase: {
      title: 'Termbase',
      description: 'Manage post-translation QA terminology and consistency checks',
    },
    glossary: {
      title: 'Glossary',
      description: 'Manage glossary entries injected into AI pre-translation context',
    },
    glossaryEdit: {
      title: 'Glossary Details',
      description: 'Maintain glossary metadata, entries, imports, and exports',
    },
    translationRules: {
      title: 'Translation Rules',
      description: 'Maintain reusable translation rules for AI translation',
    },
    termBaseEdit: {
      title: 'Termbase Details',
      description: 'Maintain termbase metadata, term entries, imports, and exports',
    },
    users: {
      title: 'User Management',
      description: 'Create users and assign system roles',
    },
    assignmentEvents: {
      title: 'Assignment Records',
      description: 'View project and file assignment, authorization, and cancellation records',
    },
  },
  shell: {
    mainNav: 'Main navigation',
    sections: {
      dashboard: 'Dashboard',
      workspace: 'Projects',
      tasks: 'My Tasks',
      assets: 'Language Assets',
      tm: 'Translation Memory',
      termBase: 'Termbase',
      glossary: 'Glossary',
      translationRules: 'Translation Rules',
      system: 'System',
      users: 'User Management',
      assignmentEvents: 'Assignment Records',
    },
    recent: {
      title: 'Recent',
      empty: 'Visited projects and workbenches will appear here.',
    },
    topbar: {
      expand: 'Expand sidebar',
      collapse: 'Collapse sidebar',
      themeLight: 'Light mode',
      themeDark: 'Dark mode',
      switchTheme: 'Switch to {name}',
      notifications: 'Notifications',
      settings: 'Settings',
      language: 'Language',
      logout: 'Log out',
      soon: '{name}, coming soon',
      markAllRead: 'Mark all as read',
      noNotifications: 'No notifications',
      globalBreadcrumb: 'Global breadcrumb',
    },
    userFallback: 'Admin',
  },
  auth: {
    login: 'Log in',
    initialize: 'Initialize super admin',
    subtitleLogin: 'Enter your account and password to open the translation workbench',
    subtitleInit: 'No users exist yet. Create a super admin account first.',
    username: 'Username',
    password: 'Password',
    submitLoading: 'Submitting...',
    statusError: 'Unable to fetch initialization status. Please make sure the backend is running.',
    requestError: 'Request failed. Please try again later.',
  },
  workbench: {
    search: {
      advanced: 'Advanced',
      sourceExcludeLabel: 'Source excludes',
      targetExcludeLabel: 'Target excludes',
      excludePlaceholder: 'Separate terms with spaces or commas',
    },
  },
  pagination: {
    total: '{total} total',
    pageSize: '{size} / page',
    jumpTo: 'Go to',
    page: 'Page',
    live: 'Page {page} of {total}',
    jumpInput: 'Jump to page',
  },
  table: {
    index: 'No.',
    actions: 'Actions',
    loading: 'Loading...',
    empty: 'No data',
    selectPage: 'Select current page',
    selectRow: 'Select row {index}',
  },
  stateView: {
    loading: 'Loading',
    empty: 'No data',
    error: 'Failed to load',
    forbidden: 'No permission',
  },
  confirm: {
    title: 'Please confirm',
  },
  errors: {
    requestFailed: 'Request failed',
    network: 'Network error. Please try again later.',
    importFailed: 'Import failed.',
  },
  appLayout: {
    brand: 'Workbench',
    collapsedBrand: 'TM',
  },
  dashboard: {
    title: 'System Dashboard',
    subtitle: 'Track source workload, translation progress, LLM usage, and team activity',
    range: {
      label: 'Range',
      day: 'By day',
      month: 'By month',
    },
    kpis: {
      projects: 'Projects',
      files: 'Files',
      translatedWords: 'Translated source words',
      llmWords: 'LLM processed source words',
      activeUsers: 'Active users today',
      progress: 'Overall progress',
    },
    empty: {
      series: 'No trend data',
      languagePairs: 'No language-pair data',
      sources: 'No source statistics',
      userStats: 'No user statistics',
    },
    errors: {
      load: 'Failed to load dashboard.',
    },
  },
  projectList: {
    searchPlaceholder: 'Search project name...',
    total: '{total} projects',
    create: 'New Project',
    importAssets: 'Import Language Assets',
    importAssetsTitle: 'Go to language assets',
    fromTemplate: 'Create from Template',
    filter: 'Filter',
    columns: 'Columns',
    selectedSummary: '{count} projects selected',
    deleteSelected: 'Delete Selected Projects',
    empty: 'No projects',
    createDialogTitle: 'New Project',
    createDialogDescription: 'After creating a project, upload documents from the project details page.',
    status: {
      confirmedProgress: 'Confirmed progress',
    },
  },
  projectDetail: {
    titleFallback: 'Project Details',
    description: 'View project progress, basic information, and file processing status.',
    back: 'Back to Projects',
    enterWorkbench: 'Open Workbench',
    loading: 'Loading project details...',
    tabs: {
      files: 'Files',
      issues: 'Issues',
      settings: 'Project Settings',
      stats: 'Statistics',
      summary: 'Summary',
      quote: 'Quote',
    },
    common: {
      comingSoon: 'Coming soon',
      placeholder: '--',
      uploadRequired: 'Upload a source document before using this action.',
      alreadyUploaded: 'This project already has uploaded source documents.',
      deleteRequiresAdmin: 'Only admins can delete projects.',
    },
    files: {
      title: 'Files',
      description: 'A project can contain multiple files. Select the language pair when uploading.',
      empty: 'No files to display.',
      openHint: 'Open the translation workbench',
      processingHint: 'The source document is uploaded and will be available after processing.',
      columns: {
        progress: 'Confirmed progress',
        pretranslationProgress: 'Pre-translation progress',
      },
      actions: {
        upload: 'Upload File',
        assign: 'Assign File',
        export: 'Export',
        exportTarget: 'Export Target File',
        exportSource: 'Export Source File',
        exportSelectFirst: 'Select files to export first.',
        exportLoading: 'Loading export options...',
        exportNoOptions: 'No common export types are available.',
        delete: 'Delete',
        filter: 'Filter',
        columns: 'Columns',
      },
    },
    stats: {
      title: 'Document Statistics',
      generate: 'Generate',
      generating: 'Generating...',
      clear: 'Clear Selection',
      selectedCount: '{count} files selected',
      total: 'Total',
      columns: {
        internalRepeatedWords: 'Internal repeated words',
        internalRepeatedCharacters: 'Internal repeated characters',
        crossFileRepeatedWords: 'Cross-file repeated words',
        crossFileRepeatedCharacters: 'Cross-file repeated characters',
      },
    },
    errors: {
      export: 'Failed to export file.',
      exportOptions: 'Failed to load export options.',
      exportSource: 'Failed to export source files.',
    },
  },
  documentParsing: {
    label: 'Document parsing',
    settingsTitle: 'Document Settings',
    selectAll: 'Select all',
    selectAllTranslatable: 'Select all translatable ranges',
    emptySettings: 'Select files to show document settings for the matching format.',
    selectedFileSummary: {
      empty: 'Select files to show the actual parsing capabilities for each format.',
      unsupported: 'The current file format is not supported by backend uploads.',
      current: 'Current files will use: {labels}.',
      loading: 'Loading backend parsing capabilities...',
      fallback: 'Parsing capabilities are unavailable. Upload validation will be used.',
    },
  },
  taskList: {
    title: 'My Tasks',
    empty: 'No tasks',
  },
  userManagement: {
    createTitle: 'Create User',
    username: 'Username',
    password: 'Password',
    role: 'Role',
    action: 'Action',
    createUser: 'Create User',
    created: 'User created',
    createFailed: 'Failed to create user.',
    admin: 'Admin',
    user: 'User',
  },
  status: {
    unknownStatus: 'Unknown status',
    unknownSource: 'Unknown source',
    file: {
      draft: 'Draft',
      inProgress: 'In progress',
      pending: 'Pending',
      processing: 'Processing',
      completed: 'Completed',
      translated: 'Translated',
      error: 'Error',
    },
    segment: {
      exact: 'Exact match',
      fuzzy: 'Fuzzy match',
      none: 'No match',
      confirmed: 'Confirmed',
      manual: 'Manual',
    },
    source: {
      manual: 'Manual',
      projectSync: 'Project sync',
    },
  },
  llm: {
    scope: {
      currentSegment: {
        label: 'Current segment',
        description: 'Only revise the currently selected segment.',
      },
      all: {
        label: 'All unconfirmed targets',
        description: 'Process fuzzy matches and unmatched segments.',
      },
      allWithExact: {
        label: 'All segments',
        description: 'Rerun exact-match segments as well.',
      },
      emptyTargetOnly: {
        label: 'Empty targets only',
        description: 'Only process segments with empty targets and keep existing translations.',
      },
      fuzzyOnly: {
        label: 'Fuzzy matches only',
        description: 'Only revise fuzzy-match segments.',
      },
      noneOnly: {
        label: 'No matches only',
        description: 'Only process segments without match results.',
      },
    },
    provider: {
      deepseek: {
        description: 'Use models provided by DeepSeek.',
      },
      auto: {
        label: 'Auto select',
        description: 'Automatically select an available model from the current configuration.',
      },
      openrouter: {
        description: 'Use models provided by OpenRouter.',
      },
    },
  },
  language: {
    uiChinese: '中文',
    uiEnglish: 'English',
  },
} satisfies LocaleTree

const phraseOverrides: Record<string, string> = {
  '全部已读': 'Mark all as read',
  '正在加载...': 'Loading...',
  '暂无消息': 'No notifications',
  '指派记录': 'Assignment Records',
  '全局页面路径': 'Global breadcrumb',
  '请求失败': 'Request failed',
  '网络异常，请稍后重试。': 'Network error. Please try again later.',
  '导入失败。': 'Import failed.',
  '确认': 'Confirm',
  '取消': 'Cancel',
  '请确认': 'Please confirm',
}

function getOverride(path: string[], source: LocaleNode): LocaleNode | undefined {
  let cursor: LocaleNode = overrides
  for (const part of path) {
    if (!cursor || typeof cursor !== 'object' || Array.isArray(cursor) || !(part in cursor)) {
      return undefined
    }
    cursor = cursor[part]
  }
  if (typeof cursor === 'string') {
    return cursor
  }
  if (cursor && typeof cursor === 'object' && !Array.isArray(cursor) && source && typeof source === 'object' && !Array.isArray(source)) {
    return undefined
  }
  return cursor
}

function humanizeKey(path: string[], source: string) {
  const direct = phraseOverrides[source]
  if (direct) {
    return direct
  }

  const key = path[path.length - 1] || 'text'
  const words = key
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
  const placeholders = Array.from(source.matchAll(/\{[^}]+\}/g), (match) => match[0])
  return placeholders.length ? `${words} ${placeholders.join(' ')}` : words
}

function localize(source: LocaleNode, path: string[] = []): LocaleNode {
  const override = getOverride(path, source)
  if (override !== undefined) {
    return override
  }
  if (typeof source === 'string') {
    return humanizeKey(path, source)
  }
  if (Array.isArray(source)) {
    return source.map((item, index) => localize(item, [...path, String(index)]))
  }
  if (source && typeof source === 'object') {
    return Object.fromEntries(
      Object.entries(source).map(([key, value]) => [key, localize(value, [...path, key])]),
    )
  }
  return source
}

export default localize(zhCN) as typeof zhCN
