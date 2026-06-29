(function () {
  "use strict";

  var sources = [
    {
      type: "dingtalk", label: "钉钉知识库",
      params: [
        { name: "mode", label: "模式", type: "enum", options: ["wiki", "doc"], default: "wiki", required: true, help: "wiki=知识库模式，doc=文件夹模式" },
        { name: "space", label: "知识库名", type: "str", help: "与 space_id 二选一（wiki 模式必填其一）" },
        { name: "space_id", label: "知识库 ID", type: "str", help: "与 space 二选一（wiki 模式必填其一）" },
        { name: "folder_id", label: "文件夹 ID", type: "str", help: "doc 模式必填" },
        { name: "folders", label: "文件夹路径", type: "list", help: "可多行，如 产品规划/解决方案" },
        { name: "include_types", label: "仅含类型", type: "list", help: "内容类型白名单，如 DOCUMENT,FILE" }
      ]
    },
    {
      type: "localdrive", label: "本地目录",
      params: [
        { name: "input_dir", label: "输入目录", type: "str", required: true, help: "本地文件系统绝对路径" },
        { name: "include", label: "包含 glob", type: "list", help: "如 *.md" },
        { name: "exclude", label: "排除 glob", type: "list", help: "如 *.tmp" }
      ]
    },
    {
      type: "tencent", label: "腾讯文档",
      params: [
        { name: "space_id", label: "知识库 ID", type: "str", required: true },
        { name: "folder_id", label: "文件夹 ID", type: "str" },
        { name: "include_types", label: "仅含类型", type: "list" }
      ]
    }
  ];

  var destinations = [
    {
      type: "hindsight", label: "Hindsight",
      params: [
        { name: "bank_id", label: "Bank ID", type: "str", envHint: "HINDSIGHT_BANK_ID" },
        { name: "api_url", label: "API URL", type: "str", envHint: "HINDSIGHT_API_URL" },
        { name: "api_key", label: "API Key", type: "str", envHint: "HINDSIGHT_API_KEY" },
        { name: "context_prefix", label: "上下文前缀", type: "str", envHint: "HINDSIGHT_CONTEXT" },
        { name: "document_id_template", label: "文档 ID 模板", type: "str", help: "可选，模板语法" },
        { name: "context_template", label: "上下文模板", type: "str" },
        { name: "extra_tags", label: "额外标签", type: "list" },
        { name: "extra_metadata", label: "额外元数据", type: "list", help: "每行 key: value" }
      ]
    },
    {
      type: "localdrive", label: "本地目录",
      params: [
        { name: "output_dir", label: "输出目录", type: "str", required: true },
        { name: "replace_extension", label: "替换扩展名为 .md", type: "bool", default: false },
        { name: "save_sidecar", label: "保存 sidecar .json", type: "bool", default: true },
        { name: "path_template", label: "路径模板", type: "str" }
      ]
    }
  ];

  var steps = [
    {
      type: "convert", label: "格式转换",
      params: [
        { name: "_note", label: "说明", type: "str", help: "由全局 converters.extensions 驱动，此处无参数。转换规则在 config 顶层 converters 配置。" }
      ]
    },
    {
      type: "image_description", label: "图片描述",
      params: [
        { name: "api_key", label: "API Key", type: "str", envHint: "OPENAI_API_KEY" },
        { name: "base_url", label: "Base URL", type: "str" },
        { name: "model", label: "模型", type: "str", default: "gpt-4o" },
        { name: "concurrency", label: "并发数", type: "int", default: 1 }
      ]
    },
    {
      type: "resolve_attachments", label: "附件解析",
      params: [
        { name: "_note", label: "说明", type: "str", help: "无参数。解析 markdown 中的本地附件引用并加入 Bundle。" }
      ]
    },
    {
      type: "tencent_delete", label: "腾讯文档删除", stage: "finalize",
      params: [
        { name: "remove_type", label: "删除类型", type: "enum", options: ["current", "all"], default: "current" }
      ]
    },
    {
      type: "excel_structured", label: "Excel 结构化",
      params: [
        { name: "fill_merged", label: "填充合并单元格", type: "bool", default: true },
        { name: "skip_hidden", label: "跳过隐藏表", type: "bool", default: true },
        { name: "skip_empty", label: "跳过空表", type: "bool", default: true }
      ]
    },
    {
      type: "s3_upload", label: "S3 上传",
      params: [
        { name: "endpoint_url", label: "Endpoint", type: "str", default: "http://localhost:9000" },
        { name: "region", label: "Region", type: "str", default: "us-east-1" },
        { name: "bucket", label: "Bucket", type: "str", required: true },
        { name: "access_key", label: "Access Key", type: "str" },
        { name: "secret_key", label: "Secret Key", type: "str" },
        { name: "prefix", label: "前缀", type: "str", default: "attachments" },
        { name: "url_prefix", label: "URL 前缀", type: "str" },
        { name: "roles", label: "处理角色", type: "list", help: "如 image，默认 image" }
      ]
    }
  ];

  function findByType(kind, type) {
    var list = kind === "source" ? sources : kind === "destination" ? destinations : steps;
    for (var i = 0; i < list.length; i++) {
      if (list[i].type === type) return list[i];
    }
    return null;
  }

  window.PipelineSchema = {
    sources: sources,
    destinations: destinations,
    steps: steps,
    findByType: findByType
  };
})();