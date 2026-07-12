import { currentSearchParams, globalValue, metaContent } from "./dom.js";

export const BENCHMARKS = {
  locomo: {
    id: "locomo",
    title: "LoCoMo 评测",
    subtitle: "先完成数据准备与记忆注入，再进入 QA、评分和报告。",
    workflowTitle: "LoCoMo 工作流",
    workflowSubtitle: "按步骤推进当前评测，而不是把所有配置同时堆在一屏里。",
    importLabel: "开始导入",
    primaryRunLabel: "开始 QA",
    stageLabels: {
      import: "开始导入",
      qa: "开始 QA",
      judge: "正式评分",
      report: "导出报告",
    },
    stageNavLabels: {
      import: "记忆注入",
      qa: "问答测试",
      judge: "结果查看",
      report: "报告产物",
    },
    datasetFormat: "locomo",
    defaultData: "./dataset/locomo10.json",
    runDetailPrefetchLimit: 2,
    questionPreviewMode: "questions_api",
    shellMeta: {
      sidebarIcon: "bookOpen",
      pageIcon: "bookOpen",
      workflowIcon: "bookOpen",
      shellLayout: "compact-workbench",
      benchmarkFamily: "memory-session",
      heroKicker: "当前页面",
      overviewLabel: "工作台概览",
      stageWorkflowTitles: {
        import: "步骤 1 · 数据准备",
        qa: "步骤 2 · 问答测试",
        judge: "步骤 3 · 结果确认",
        report: "步骤 4 · 报告产物",
      },
      stageWorkflowSubtitles: {
        import: "先确认数据集、会话范围和记忆目录，再启动导入。",
        qa: "导入完成后，再配置题目范围、参数和运行模式。",
        judge: "优先确认当前结果，再决定是否正式判分或补跑。",
        report: "先看当前结果，历史记录和导出产物按需展开。",
      },
      primaryActionIcons: {
        import: "uploadCloud",
        qa: "play",
        judge: "barChart3",
        report: "folderArchive",
      },
      stagePresentation: {
        import: { icon: "uploadCloud", subtitle: "准备数据、写入记忆、检查状态", tone: "setup" },
        qa: { icon: "messagesSquare", subtitle: "配置题量、运行问答、查看当前任务", tone: "run" },
        judge: { icon: "barChart3", subtitle: "优先确认当前结果，再决定是否重跑或判分", tone: "verify" },
        report: { icon: "folderArchive", subtitle: "当前结果优先，其他报告与历史默认折叠查看", tone: "report" },
      },
    },
  },
  hotpotqa: {
    id: "hotpotqa",
    title: "HotpotQA 评测",
    subtitle: "保留原始评测能力，但前端代码和布局完全重写，不再受旧样式影响。",
    workflowTitle: "HotpotQA 工作流",
    workflowSubtitle: "同样分成记忆注入、问答测试、评分、测试报告四段，整体排版对齐 LoCoMo。",
    importLabel: "开始注入",
    primaryRunLabel: "开始测试",
    stageLabels: {
      import: "开始注入",
      qa: "开始测试",
      judge: "前往报告",
      report: "导出报告",
    },
    stageNavLabels: {
      import: "记忆注入",
      qa: "问答测试",
      judge: "结果查看",
      report: "报告产物",
    },
    datasetFormat: "hotpotqa",
    defaultData: "./dataset/full/hotpotqa_dev_distractor.json",
    runDetailPrefetchLimit: 12,
    questionPreviewMode: "summary_only",
    shellMeta: {
      sidebarIcon: "bookOpen",
      pageIcon: "bookOpen",
      workflowIcon: "bookOpen",
      shellLayout: "reference-workbench",
      benchmarkFamily: "document-qa",
      heroKicker: "共享壳层",
      overviewLabel: "Workbench Snapshot",
      primaryActionIcons: {
        import: "uploadCloud",
        qa: "play",
        judge: "barChart3",
        report: "folderArchive",
      },
      stagePresentation: {
        import: { icon: "uploadCloud", subtitle: "准备数据、写入记忆、检查状态", tone: "setup" },
        qa: { icon: "messagesSquare", subtitle: "配置题量、运行问答、查看当前任务", tone: "run" },
        judge: { icon: "barChart3", subtitle: "查看当前结果、确认官方评测状态", tone: "verify" },
        report: { icon: "folderArchive", subtitle: "优先看当前结果，再展开历史和产物", tone: "report" },
      },
    },
    officialEvalMeta: {
      benchmarkName: "HotpotQA",
      viewModelMode: "answer_f1_bundle",
      previewMode: "hotpotqa",
      configMode: "hotpotqa",
      qaFormMode: "hotpotqa",
      previewTitle: "HotpotQA 当前结果",
      previewSubtitleScope: true,
      judgeDescriptionMode: "scope_prefixed",
      judgeNoteDefault: "结果以运行后自动生成的官方 HotpotQA 评测为主。",
      reportTitle: "HotpotQA 报告产物",
      reportSummaryFilename: "hotpotqa_answer_summary.json",
      summaryReadyMode: "hotpotqa",
      importRunNamePattern: "hotpotqa import",
      scoreMode: "answer_f1",
      gradedMode: "official_then_summary",
      correctMode: "answer_em_rows",
      wrongMode: "rows_minus_correct",
    },
  },
  longmemeval: {
    id: "longmemeval",
    title: "LongMemEval 评测",
    subtitle: "复用 generic QA 评测链路，把长时记忆问答、官方判分和报告产物统一收进 V2。",
    workflowTitle: "Benchmark Workbench",
    workflowSubtitle: "优先完成当前步骤，结果、历史与诊断按需展开。",
    importLabel: "开始注入",
    primaryRunLabel: "开始测试",
    stageLabels: {
      import: "开始注入",
      qa: "开始测试",
      judge: "前往报告",
      report: "导出报告",
    },
    stageNavLabels: {
      import: "记忆注入",
      qa: "问答测试",
      judge: "结果查看",
      report: "报告产物",
    },
    datasetFormat: "longmemeval",
    defaultData: "./dataset/full/longmemeval_s_cleaned.json",
    runDetailPrefetchLimit: 12,
    questionPreviewMode: "summary_only",
    shellMeta: {
      sidebarIcon: "clipboardList",
      pageIcon: "clipboardList",
      workflowIcon: "uploadCloud",
      shellLayout: "compact-workbench",
      benchmarkFamily: "memory-session",
      heroKicker: "Benchmark",
      overviewLabel: "Workbench Snapshot",
      primaryActionIcons: {
        import: "uploadCloud",
        qa: "play",
        judge: "barChart3",
        report: "folderArchive",
      },
      stagePresentation: {
        import: { icon: "uploadCloud", subtitle: "准备数据、写入记忆、检查状态", tone: "setup" },
        qa: { icon: "messagesSquare", subtitle: "配置题量、运行问答、查看当前任务", tone: "run" },
        judge: { icon: "barChart3", subtitle: "查看当前结果、确认官方评测状态", tone: "verify" },
        report: { icon: "folderArchive", subtitle: "优先看当前结果，再展开历史和产物", tone: "report" },
      },
    },
    officialEvalMeta: {
      benchmarkName: "LongMemEval",
      viewModelMode: "overall_accuracy_bundle",
      previewMode: "longmemeval",
      configMode: "longmemeval",
      qaFormMode: "longmemeval",
      previewTitle: "LongMemEval 当前结果",
      previewSubtitleScope: false,
      judgeDescriptionMode: "official_summary",
      judgeNoteDefault: "结果以运行后自动生成的官方 LongMemEval 评测为主。",
      reportTitle: "LongMemEval 报告产物",
      reportSummaryFilename: "longmemeval_official_summary.json",
      summaryReadyMode: "longmemeval",
      importRunNamePattern: "longmemeval import",
      scoreMode: "overall_accuracy",
      gradedMode: "summary_official_then_summary",
      correctMode: "official_correct",
      wrongMode: "official_wrong",
    },
  },
};

function searchParams() {
  return currentSearchParams();
}

export function resolveStandaloneApiBase() {
  const params = searchParams();
  return String(
    globalValue("BENCHMARK_CONSOLE_API_BASE")
    || params.get("apiBase")
    || ""
  ).trim().replace(/\/$/, "");
}

export function resolveReferenceUrl() {
  const params = searchParams();
  return String(
    params.get("referenceUrl")
    || metaContent("benchmark-console-reference-url")
    || globalValue("BENCHMARK_CONSOLE_REFERENCE_URL")
    || ""
  ).trim().replace(/\/$/, "");
}
