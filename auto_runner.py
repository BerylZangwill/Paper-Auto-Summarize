"""
论文身份卡自动提取脚本 v2.0
============================================================
功能：
1. 自动同步主题桶配置到文件夹结构
2. 支持按主题桶分文件夹管理PDF
3. 交互式选择处理哪些文件夹
4. 支持断点续跑 / 覆盖重跑
5. JSON按文件夹分组存储，CSV分文件夹+总汇总

文件结构：
============================================================
project/
├── auto_runner.py              ← 主脚本
├── prompt_paper_extraction.md  ← Prompt模板
├── theme_buckets.md               ← 主题桶配置
├── 00_inbox_pdfs/                 ← PDF输入（按主题分文件夹）
│   ├── 教育数智化与智能治理/
│   │   ├── 01_论文A.pdf
│   │   └── 02_论文B.pdf
│   └── 教育治理与治理体系现代化/
│       └── 01_论文C.pdf
├── 01_extracted_json/             ← JSON输出（按文件夹分组）
├── 02_summary_csv/                ← CSV输出
│   ├── 教育数智化与智能治理.csv
│   └── _all_papers.csv            ← 总汇总
└── error_log.txt                  ← 错误日志

使用方法：
============================================================
1. 设置环境变量：
   Windows CMD:    set DEEPSEEK_API_KEY=your_key
   Windows PS:     $env:DEEPSEEK_API_KEY="your_key"
   Linux/Mac:      export DEEPSEEK_API_KEY="your_key"

2. 安装依赖：
   pip install openai pymupdf

3. 运行脚本：
   python auto_runner.py
"""

import os
import re
import sys
import time
import json
import csv
from pathlib import Path
from dataclasses import dataclass, field
from openai import OpenAI
import fitz  # PyMuPDF

# ============================================================
# 配置区域
# ============================================================
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL = "https://api.deepseek.com"
MODEL_NAME = "deepseek-chat"

BASE_DIR = Path(__file__).parent.resolve()
PDF_DIR = BASE_DIR / "00_inbox_pdfs"
JSON_DIR = BASE_DIR / "01_extracted_json"
CSV_DIR = BASE_DIR / "02_summary_csv"
PROMPT_FILE = BASE_DIR / "prompt_paper_extraction.md"
THEME_FILE = BASE_DIR / "theme_buckets.md"
ERROR_LOG = BASE_DIR / "error_log.txt"

REQUEST_TIMEOUT = 180.0
REQUEST_INTERVAL = 5
MAX_PDF_CHARS = 30000

ALL_CSV_NAME = "_all_papers.csv"

# ============================================================
# CSV字段配置（按用户期望顺序排列）
# ============================================================
CSV_FIELD_MAP = {
    "index": "编号",
    "year": "年份",
    "venue": "期刊",
    "authors": "作者",
    "title": "标题",
    "paper_type": "论文类型",
    "domain_tags": "领域标签",
    "keywords": "关键词",
    "research_object": "研究对象",
    "methodology": "研究方法",
    "problem": "研究问题",
    "conclusion": "核心结论",
    "implementation_path": "具体实现路径_简明",
    "contribution": "主要贡献",
    "score_rigor": "学术严谨度",
    "score_innovation": "创新程度",
    "score_practicality": "实用价值",
    "score_impact": "影响范围",
    "score_readability": "可读性",
    "overall_score": "综合评分",
    "recommendation_level": "推荐等级",
}

CSV_FIELDS = list(CSV_FIELD_MAP.keys())

# ============================================================
# 数据结构
# ============================================================
@dataclass
class FolderStatus:
    """文件夹状态"""
    name: str
    path: Path
    total_pdfs: int = 0
    processed: int = 0
    pending: int = 0
    pdf_files: list = field(default_factory=list)

# ============================================================
# 工具函数
# ============================================================
client = None


def print_separator(char="=", length=70):
    print(char * length)


def print_header(text: str):
    print_separator()
    print(f"  {text}")
    print_separator()


def get_display_width(s: str) -> int:
    """计算字符串的显示宽度（中文/全角=2，其他=1）"""
    import unicodedata
    width = 0
    for char in s:
        ea = unicodedata.east_asian_width(char)
        if ea in ('F', 'W'):  # Fullwidth, Wide
            width += 2
        else:
            width += 1
    return width


def pad_center(s: str, width: int) -> str:
    """居中对齐到指定显示宽度"""
    current = get_display_width(s)
    if current >= width:
        return s
    total_pad = width - current
    left = total_pad // 2
    right = total_pad - left
    return ' ' * left + s + ' ' * right


def pad_left(s: str, width: int) -> str:
    """左对齐到指定显示宽度"""
    current = get_display_width(s)
    if current >= width:
        return s
    return s + ' ' * (width - current)


def init_client() -> bool:
    global client
    if not API_KEY:
        return False
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    return True


def log_error(filename: str, error: str):
    """记录错误到日志文件"""
    with open(ERROR_LOG, 'a', encoding='utf-8') as f:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{timestamp}] {filename}: {error}\n")


def extract_number_from_filename(filename: str) -> str:
    """从文件名提取编号前缀（如 01_xxx.pdf -> 01）"""
    match = re.match(r'^(\d+)[_\-]', filename)
    return match.group(1) if match else ""


def format_list_as_numbered(val) -> str:
    """将数组转为编号文本，单元素直接返回"""
    if isinstance(val, list):
        if len(val) == 0:
            return ""
        if len(val) == 1:
            return str(val[0])
        return "\n".join(f"{i+1}. {item}" for i, item in enumerate(val))
    return str(val) if val is not None else ""


def extract_scores_from_json(json_obj: dict) -> dict:
    """
    从JSON中提取评分信息

    Args:
        json_obj: LLM返回的完整JSON对象

    Returns:
        dict: {score_rigor, score_innovation, score_practicality, score_impact, score_readability, overall_score, recommendation_level}
    """
    scores = json_obj.get("scores", {})
    recommendation = json_obj.get("recommendation", {})

    return {
        "score_rigor": scores.get("rigor", ""),
        "score_innovation": scores.get("innovation", ""),
        "score_practicality": scores.get("practicality", ""),
        "score_impact": scores.get("impact", ""),
        "score_readability": scores.get("readability", ""),
        "overall_score": scores.get("overall", ""),
        "recommendation_level": recommendation.get("level", ""),
    }


def format_implementation_path(impl_path_dict) -> str:
    """
    将implementation_path字典转为CSV简明格式

    格式: 维度 [关键词1 | 关键词2] → 描述

    Args:
        impl_path_dict: implementation_path字段的值（字典或其他格式）

    Returns:
        str: 格式化后的字符串（多行时用\n分隔）
    """
    if not impl_path_dict:
        return ""

    # 如果是字符串（旧格式兼容），直接返回
    if isinstance(impl_path_dict, str):
        return impl_path_dict

    # 如果不是字典，转为字符串返回
    if not isinstance(impl_path_dict, dict):
        return str(impl_path_dict) if impl_path_dict else ""

    lines = []
    for idx, (key, value) in enumerate(impl_path_dict.items(), 1):
        if isinstance(value, dict):
            # 新结构：包含description和keywords
            keywords = value.get("keywords", [])
            description = value.get("description", "").strip()

            # 构建关键词部分
            if keywords:
                keywords_str = " | ".join(str(kw) for kw in keywords)
                lines.append(f"{idx}. {key} [{keywords_str}]")
            else:
                lines.append(f"{idx}. {key}")

            # 添加描述行
            if description:
                lines.append(f"   → {description}")
        else:
            # 兼容旧格式（纯字符串）
            lines.append(f"{idx}. {key}: {value}")

    return "\n".join(lines)


# ============================================================
# 步骤1: 检查配置文件
# ============================================================
def check_required_files() -> bool:
    """检查必要文件是否存在"""
    print("\n📁 检查配置文件...")
    all_ok = True
    
    checks = [
        (PROMPT_FILE, "Prompt模板"),
        (THEME_FILE, "主题桶配置"),
    ]
    
    for path, name in checks:
        if path.exists():
            print(f"   ✅ {name}: {path.name}")
        else:
            print(f"   ❌ {name}不存在: {path}")
            all_ok = False
    
    # PDF目录不存在则创建
    if not PDF_DIR.exists():
        PDF_DIR.mkdir(parents=True)
        print(f"   ✅ PDF目录: {PDF_DIR.name} (已创建)")
    else:
        print(f"   ✅ PDF目录: {PDF_DIR.name}")
    
    return all_ok


# ============================================================
# 步骤2: 同步文件夹结构
# ============================================================
def parse_theme_buckets() -> list[str]:
    """从主题桶配置文件解析一级主题名称"""
    content = THEME_FILE.read_text(encoding='utf-8')
    themes = []
    for line in content.split('\n'):
        line = line.strip()
        if line.startswith('## ') and not line.startswith('## '):
            theme_name = line[3:].strip()
            if theme_name:
                themes.append(theme_name)
        elif line.startswith('##'):
            theme_name = line[2:].strip()
            if theme_name:
                themes.append(theme_name)
    return themes


def sync_folder_structure() -> None:
    """根据主题桶配置同步文件夹结构"""
    print("\n📂 同步文件夹结构...")
    
    themes = parse_theme_buckets()
    print(f"   从主题桶配置中解析到 {len(themes)} 个主题")
    
    if not themes:
        print("   ⚠️ 未解析到任何主题，请检查theme_buckets.md格式")
        return
    
    # 获取已存在的文件夹
    existing_folders = set()
    if PDF_DIR.exists():
        for item in PDF_DIR.iterdir():
            if item.is_dir():
                existing_folders.add(item.name)
    
    # 创建缺失的文件夹
    created_count = 0
    for theme in themes:
        folder_path = PDF_DIR / theme
        if theme not in existing_folders:
            folder_path.mkdir(parents=True, exist_ok=True)
            print(f"   ✅ 新建: {theme}")
            created_count += 1
    
    skipped_count = len(themes) - created_count
    if skipped_count > 0:
        print(f"   ⏩ 已存在: {skipped_count} 个文件夹（跳过）")
    
    print("   同步完成")


# ============================================================
# 步骤3: 测试API连接
# ============================================================
def test_api_connection() -> bool:
    """测试API连接"""
    print("\n🔑 测试API连接...")
    print(f"   端点: {BASE_URL}")
    print(f"   模型: {MODEL_NAME}")
    
    if not API_KEY:
        print("   ❌ API Key为空，请设置环境变量 DEEPSEEK_API_KEY")
        return False
    
    if not init_client():
        print("   ❌ 客户端初始化失败")
        return False
    
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": "测试"}],
            max_tokens=10,
            timeout=30.0
        )
        if response and response.choices[0].message.content:
            print("   ✅ API连接成功")
            return True
    except Exception as e:
        print(f"   ❌ 连接失败: {e}")
    
    return False


# ============================================================
# 步骤4: 扫描PDF文件
# ============================================================
def get_json_path_for_pdf(pdf_path: Path, folder_name: str) -> Path:
    """根据PDF路径生成对应的JSON路径"""
    return JSON_DIR / folder_name / (pdf_path.stem + ".json")


def scan_folders() -> tuple[list[FolderStatus], list[Path]]:
    """
    扫描PDF目录
    返回: (有PDF的文件夹列表, 根目录散落的PDF列表)
    """
    folders = {}
    root_pdfs = []
    
    # 扫描所有PDF
    for item in PDF_DIR.iterdir():
        if item.is_file() and item.suffix.lower() == '.pdf':
            # 根目录的PDF
            root_pdfs.append(item)
        elif item.is_dir():
            # 子文件夹
            folder_name = item.name
            pdf_files = sorted(
                [f for f in item.iterdir() if f.is_file() and f.suffix.lower() == '.pdf'],
                key=lambda x: x.name
            )
            
            if pdf_files:  # 只记录有PDF的文件夹
                folder = FolderStatus(
                    name=folder_name,
                    path=item,
                    total_pdfs=len(pdf_files),
                    pdf_files=pdf_files
                )
                
                # 统计已处理数量
                for pdf_path in pdf_files:
                    json_path = get_json_path_for_pdf(pdf_path, folder_name)
                    if json_path.exists():
                        folder.processed += 1
                    else:
                        folder.pending += 1
                
                folders[folder_name] = folder
    
    # 按名称排序
    result = list(folders.values())
    result.sort(key=lambda x: x.name)
    
    return result, root_pdfs


def display_folder_status(folders: list[FolderStatus], root_pdfs: list[Path], empty_folder_count: int):
    """显示文件夹状态表"""
    print("\n📊 扫描PDF文件...")
    
    # 警告根目录PDF
    if root_pdfs:
        print(f"\n   ⚠️ 警告: 发现 {len(root_pdfs)} 个PDF在根目录，请移到子文件夹")
        for pdf in root_pdfs[:5]:  # 最多显示5个
            print(f"      - {pdf.name}")
        if len(root_pdfs) > 5:
            print(f"      ... 等共 {len(root_pdfs)} 个文件")
        print("   这些文件将被跳过")
    
    if not folders:
        print("\n   ⚠️ 未找到任何PDF文件（请将PDF放入子文件夹中）")
        return
    
    # 表格列宽定义（显示宽度）
    COL_NO = 6       # 序号列
    COL_NAME = 32    # 文件夹名称列
    COL_NUM = 8      # 数字列

    # 表格
    print()
    print(f"┌{'─'*COL_NO}┬{'─'*COL_NAME}┬{'─'*COL_NUM}┬{'─'*COL_NUM}┬{'─'*COL_NUM}┐")
    print(f"│{pad_center('序号', COL_NO)}│{pad_center('文件夹名称', COL_NAME)}│{pad_center('PDF总数', COL_NUM)}│{pad_center('已处理', COL_NUM)}│{pad_center('待处理', COL_NUM)}│")
    print(f"├{'─'*COL_NO}┼{'─'*COL_NAME}┼{'─'*COL_NUM}┼{'─'*COL_NUM}┼{'─'*COL_NUM}┤")

    for i, folder in enumerate(folders, 1):
        name_display = folder.name
        # 截断过长的名称（按显示宽度计算）
        if get_display_width(name_display) > COL_NAME - 2:
            while get_display_width(name_display + "...") > COL_NAME - 2:
                name_display = name_display[:-1]
            name_display = name_display + "..."

        no_str = f"[{i}]"
        print(f"│{pad_center(no_str, COL_NO)}│ {pad_left(name_display, COL_NAME - 1)}│{pad_center(str(folder.total_pdfs), COL_NUM)}│{pad_center(str(folder.processed), COL_NUM)}│{pad_center(str(folder.pending), COL_NUM)}│")

    # 汇总行
    total_pdfs = sum(f.total_pdfs for f in folders)
    total_processed = sum(f.processed for f in folders)
    total_pending = sum(f.pending for f in folders)

    print(f"├{'─'*COL_NO}┼{'─'*COL_NAME}┼{'─'*COL_NUM}┼{'─'*COL_NUM}┼{'─'*COL_NUM}┤")
    print(f"│{pad_center('合计', COL_NO)}│{' '*COL_NAME}│{pad_center(str(total_pdfs), COL_NUM)}│{pad_center(str(total_processed), COL_NUM)}│{pad_center(str(total_pending), COL_NUM)}│")
    print(f"└{'─'*COL_NO}┴{'─'*COL_NAME}┴{'─'*COL_NUM}┴{'─'*COL_NUM}┴{'─'*COL_NUM}┘")
    
    # 空文件夹提示
    if empty_folder_count > 0:
        print(f"\n   💡 提示: 有 {empty_folder_count} 个空文件夹未显示（无PDF）")


# ============================================================
# 步骤5: 用户交互
# ============================================================
def get_user_choice(folders: list[FolderStatus]) -> tuple[str, list[int], bool]:
    """
    获取用户选择
    返回: (操作模式, 选中的文件夹索引列表, 是否覆盖)
    """
    print("\n请选择操作模式:")
    print("  [A] 全部执行 - 处理所有待处理的PDF（跳过已处理）")
    print("  [R] 全部重跑 - 重新处理所有PDF（覆盖已有结果）")
    print("  [S] 选择执行 - 选择特定文件夹处理")
    print("  [Q] 退出")
    print()
    
    while True:
        choice = input("请输入选项 [A/R/S/Q]: ").strip().upper()
        
        if choice == 'Q':
            return ('quit', [], False)
        
        elif choice == 'A':
            indices = [i for i, f in enumerate(folders) if f.pending > 0]
            if not indices:
                print("   ⚠️ 没有待处理的文件")
                return ('quit', [], False)
            return ('all', indices, False)
        
        elif choice == 'R':
            confirm = input("   ⚠️ 确认覆盖所有已处理的结果? [y/N]: ").strip().lower()
            if confirm == 'y':
                indices = [i for i, f in enumerate(folders) if f.total_pdfs > 0]
                return ('all', indices, True)
            else:
                print("   已取消")
                continue
        
        elif choice == 'S':
            return get_folder_selection(folders)
        
        else:
            print("   ❌ 无效选项，请重新输入")


def get_folder_selection(folders: list[FolderStatus]) -> tuple[str, list[int], bool]:
    """获取用户选择的文件夹"""
    print()
    print("请输入要处理的文件夹序号:")
    print("  - 单个: 1")
    print("  - 多个: 1,3,5")
    print("  - 范围: 1-5")
    print("  - 混合: 1,3-5,7")
    print("  - 返回: B")
    print()
    
    while True:
        selection = input("请输入序号: ").strip()
        
        if selection.upper() == 'B':
            return get_user_choice(folders)
        
        try:
            indices = parse_selection(selection, len(folders))
            if not indices:
                print("   ❌ 未选择任何文件夹")
                continue
            
            # 显示选中的文件夹
            print(f"\n   已选择 {len(indices)} 个文件夹:")
            for i in indices:
                f = folders[i]
                print(f"     - {f.name} (待处理: {f.pending})")
            
            # 询问是否覆盖
            has_processed = any(folders[i].processed > 0 for i in indices)
            overwrite = False
            if has_processed:
                print()
                print("   检测到已有处理结果，请选择:")
                print("   [N] 跳过已处理（默认）")
                print("   [O] 覆盖已处理")
                ow_choice = input("   请选择 [N/O]: ").strip().upper()
                overwrite = (ow_choice == 'O')
            
            confirm = input("\n   确认开始处理? [Y/n]: ").strip().lower()
            if confirm in ('', 'y', 'yes'):
                return ('select', indices, overwrite)
            else:
                print("   已取消")
                continue
                
        except ValueError as e:
            print(f"   ❌ {e}")
            continue


def parse_selection(selection: str, max_num: int) -> list[int]:
    """解析用户输入的序号"""
    indices = set()
    parts = selection.replace(' ', '').split(',')
    
    for part in parts:
        if not part:
            continue
        if '-' in part:
            try:
                start, end = part.split('-')
                start, end = int(start), int(end)
                if start < 1 or end > max_num or start > end:
                    raise ValueError(f"范围 {part} 无效")
                indices.update(range(start - 1, end))
            except:
                raise ValueError(f"无法解析范围: {part}")
        else:
            try:
                num = int(part)
                if num < 1 or num > max_num:
                    raise ValueError(f"序号 {num} 超出范围 (1-{max_num})")
                indices.add(num - 1)
            except ValueError:
                raise ValueError(f"无法解析序号: {part}")
    
    return sorted(list(indices))


# ============================================================
# 步骤6: 核心处理逻辑
# ============================================================
def load_theme_buckets() -> str:
    """读取主题桶配置文件内容"""
    return THEME_FILE.read_text(encoding='utf-8')


def load_scoring_config(domain_tag: str) -> dict:
    """
    动态加载论文领域对应的评分配置

    当前版本（P0）：始终返回标准权重
    后期版本（P1+）：根据domain_tag加载对应领域的scoring_*.md文件

    Args:
        domain_tag: 论文的主题桶名称（如 "人工智能教育应用"）

    Returns:
        dict: 权重配置 {rigor, innovation, practicality, impact, readability}
    """
    # TODO: P1 实现动态加载
    # domain_mapping = {
    #     "人工智能教育应用": "education",
    #     "教育理论研究": "education",
    #     "工程应用": "engineering",
    #     ...
    # }
    # domain = domain_mapping.get(domain_tag, "framework")
    # scoring_file = BASE_DIR / "scoring" / f"scoring_{domain}.md"
    # if scoring_file.exists():
    #     return parse_scoring_config_from_markdown(scoring_file)

    # 当前阶段：直接返回P0默认权重
    return {
        "rigor": 0.30,
        "innovation": 0.25,
        "practicality": 0.25,
        "impact": 0.15,
        "readability": 0.05
    }


def load_prompt_template() -> str:
    """读取Prompt模板"""
    return PROMPT_FILE.read_text(encoding='utf-8')


def build_full_prompt(prompt_template: str, theme_content: str, pdf_text: str) -> str:
    """构建完整的Prompt"""
    prompt_with_theme = prompt_template.replace("{THEME_BUCKETS}", theme_content)
    return f"""{prompt_with_theme}

---

以下是论文的全文内容，请按照上述要求进行信息提取：

{pdf_text}
"""


def extract_pdf_text(pdf_path: Path) -> str:
    """提取PDF文本内容"""
    text_parts = []
    doc = fitz.open(str(pdf_path))
    for page_num, page in enumerate(doc, 1):
        text = page.get_text("text")
        if text.strip():
            text_parts.append(f"--- 第 {page_num} 页 ---\n{text}")
    doc.close()
    
    full_text = "\n\n".join(text_parts)
    if len(full_text) > MAX_PDF_CHARS:
        full_text = full_text[:MAX_PDF_CHARS] + "\n\n[注: 文本已截断]"
    
    return full_text


def clean_json_response(text: str) -> str:
    """清洗模型返回的JSON"""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def call_llm(full_prompt: str) -> str:
    """调用LLM API"""
    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {
                "role": "system",
                "content": "你是一个专业的学术论文分析助手。请严格按照用户提供的JSON Schema格式输出结果，只输出JSON，不要添加任何额外说明。"
            },
            {
                "role": "user",
                "content": full_prompt
            }
        ],
        temperature=0.1,
        max_tokens=4096,
        timeout=REQUEST_TIMEOUT
    )
    return response.choices[0].message.content


def init_csv(csv_path: Path):
    """初始化CSV文件"""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if not csv_path.exists():
        with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            headers = [CSV_FIELD_MAP[k] for k in CSV_FIELDS]
            writer.writerow(headers)


def append_to_csv(csv_path: Path, data: dict, index_value: str = ""):
    """
    追加数据到CSV

    Args:
        csv_path: CSV文件路径
        data: 论文数据字典
        index_value: 编号值（分主题表用文件名编号，总表用全局编号）
    """
    # 先提取评分信息（如果JSON中包含scores字段）
    scores_data = extract_scores_from_json(data)

    row = []
    for key in CSV_FIELDS:
        if key == "index":
            # 编号字段使用传入的index_value
            row.append(index_value)
        elif key in ("problem", "conclusion"):
            # 研究问题和结论使用编号列表格式
            val = data.get(key, "")
            row.append(format_list_as_numbered(val))
        elif key == "implementation_path":
            # 实现路径使用格式化函数
            val = data.get(key, "")
            row.append(format_implementation_path(val))
        elif key in ("keywords", "domain_tags"):
            # 关键词和领域标签使用逗号分隔
            val = data.get(key, [])
            if isinstance(val, list):
                row.append(", ".join(str(v) for v in val))
            else:
                row.append(str(val) if val else "")
        elif key in ("score_rigor", "score_innovation", "score_practicality", "score_impact", "score_readability", "overall_score", "recommendation_level"):
            # 评分字段从scores_data中取
            val = scores_data.get(key, "")
            row.append(str(val) if val else "")
        else:
            val = data.get(key, "")
            if val is None:
                val = ""
            row.append(str(val))

    with open(csv_path, 'a', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(row)


def process_folder(
    folder: FolderStatus,
    prompt_template: str,
    theme_content: str,
    overwrite: bool = False,
    global_index_start: int = 1
) -> tuple[dict, int]:
    """
    处理单个文件夹

    Args:
        folder: 文件夹状态
        prompt_template: Prompt模板
        theme_content: 主题桶内容
        overwrite: 是否覆盖已有结果
        global_index_start: 全局编号起始值

    Returns:
        (统计字典, 下一个全局编号)
    """
    stats = {"success": 0, "skip": 0, "fail": 0}
    global_index = global_index_start
    
    # 确定输出路径
    json_folder = JSON_DIR / folder.name
    json_folder.mkdir(parents=True, exist_ok=True)
    
    csv_path = CSV_DIR / f"{folder.name}.csv"
    all_csv_path = CSV_DIR / ALL_CSV_NAME
    
    # 覆盖模式下删除已有CSV
    if overwrite and csv_path.exists():
        csv_path.unlink()
    
    init_csv(csv_path)
    init_csv(all_csv_path)
    
    # 计算待处理数量
    to_process = []
    for pdf_path in folder.pdf_files:
        json_path = json_folder / (pdf_path.stem + ".json")
        if overwrite or not json_path.exists():
            to_process.append(pdf_path)
        else:
            stats["skip"] += 1
    
    if not to_process:
        return stats, global_index
    
    # 处理每个PDF
    for i, pdf_path in enumerate(to_process, 1):
        json_path = json_folder / (pdf_path.stem + ".json")
        
        print(f"\n   [{i}/{len(to_process)}] {pdf_path.name}")
        
        try:
            # 提取PDF文本
            print(f"      📄 提取文本...", end=" ")
            pdf_text = extract_pdf_text(pdf_path)
            print(f"{len(pdf_text)}字符")
            
            # 调用LLM
            print(f"      🤖 调用LLM...")
            start_time = time.time()
            response_text = call_llm(
                build_full_prompt(prompt_template, theme_content, pdf_text)
            )
            elapsed = time.time() - start_time
            print(f"      ✅ 响应完成 ({elapsed:.1f}秒)")
            
            # 解析JSON
            clean_text = clean_json_response(response_text)
            data = json.loads(clean_text)
            
            # 添加来源文件夹
            data["source_folder"] = folder.name
            
            # 保存JSON
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"      💾 JSON 保存成功")
            
            # 提取本地编号（从文件名）
            local_index = extract_number_from_filename(pdf_path.name)

            # 追加CSV（分主题表用本地编号，总表用全局编号）
            append_to_csv(csv_path, data, index_value=local_index)
            append_to_csv(all_csv_path, data, index_value=str(global_index))
            print(f"      📝 CSV 更新成功 (本地编号: {local_index}, 全局编号: {global_index})")

            stats["success"] += 1
            global_index += 1
            
        except json.JSONDecodeError as e:
            print(f"      ❌ JSON解析失败: {e}")
            stats["fail"] += 1
            log_error(pdf_path.name, f"JSON解析失败: {e}")
            
        except Exception as e:
            print(f"      ❌ 处理失败: {e}")
            stats["fail"] += 1
            log_error(pdf_path.name, str(e))
        
        # 请求间隔
        if i < len(to_process):
            print(f"      ⏳ 等待{REQUEST_INTERVAL}秒...")
            time.sleep(REQUEST_INTERVAL)

    return stats, global_index


# ============================================================
# 主函数
# ============================================================
def main():
    print_header("📚 论文身份卡自动提取工具 v2.0")
    
    # 步骤1: 检查配置文件
    if not check_required_files():
        print("\n⚠️ 请检查配置文件后重试")
        return
    
    # 步骤2: 同步文件夹结构
    sync_folder_structure()
    
    # 步骤3: 测试API连接
    if not test_api_connection():
        print("\n⚠️ API连接失败，请检查配置")
        return
    
    # 步骤4: 扫描PDF文件
    folders, root_pdfs = scan_folders()
    
    # 计算空文件夹数量
    themes = parse_theme_buckets()
    existing_folder_names = {f.name for f in folders}
    empty_folder_count = len([t for t in themes if t not in existing_folder_names])
    
    # 显示状态
    display_folder_status(folders, root_pdfs, empty_folder_count)
    
    if not folders:
        return
    
    # 步骤5: 用户选择
    mode, selected_indices, overwrite = get_user_choice(folders)
    
    if mode == 'quit':
        print("\n👋 已退出")
        return
    
    # 步骤6: 加载配置并处理
    print("\n📂 加载配置文件...")
    prompt_template = load_prompt_template()
    theme_content = load_theme_buckets()
    print("   ✅ 配置已加载")
    
    # 创建输出目录
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    
    # 处理选中的文件夹
    total_stats = {"success": 0, "skip": 0, "fail": 0}
    global_index = 1  # 全局编号从1开始

    for idx in selected_indices:
        folder = folders[idx]

        # 无待处理且非覆盖模式则跳过
        if not overwrite and folder.pending == 0:
            continue

        print_separator("-")
        print("🚀 开始任务")
        print_separator("-")
        print(f"📁 正在处理: {folder.name}")
        print(f"   PDF数量: {folder.total_pdfs} | 已处理: {folder.processed} | 待处理: {folder.pending}")
        print_separator("-")

        stats, global_index = process_folder(
            folder, prompt_template, theme_content, overwrite, global_index
        )

        for key in total_stats:
            total_stats[key] += stats[key]

        print(f"\n   📊 本文件夹完成: 成功 {stats['success']} | 跳过 {stats['skip']} | 失败 {stats['fail']}")
    
    # 最终汇总
    print_separator()
    print("📊 全部处理完成")
    print_separator()
    print(f"   ✅ 成功: {total_stats['success']}")
    print(f"   ⏩ 跳过: {total_stats['skip']}")
    print(f"   ❌ 失败: {total_stats['fail']}")
    print()
    print(f"   📂 输出位置:")
    print(f"      JSON: {JSON_DIR}/")
    print(f"      CSV:  {CSV_DIR}/")
    print(f"      汇总: {CSV_DIR / ALL_CSV_NAME}")
    
    if total_stats["fail"] > 0:
        print(f"      错误日志: {ERROR_LOG}")
    
    print_separator()


if __name__ == "__main__":
    main()
