"""
论文数据可视化模块
============================================================
功能：
1. 关键词词云
2. 论文类型分布图
3. 年份-主题热力图

使用方法：
    python visualize.py
    python visualize.py --input 02_summary_csv/_all_papers.csv
    python visualize.py --output 03_visualizations
"""

import argparse
import sys
from pathlib import Path

# 检查依赖
try:
    import pandas as pd
    import matplotlib
    matplotlib.use('Agg')  # 非交互式后端，必须在导入pyplot之前
    import matplotlib.pyplot as plt
except ImportError as e:
    print(f"[ERROR] Missing dependency: {e}")
    print("Please install: pip install matplotlib pandas")
    sys.exit(1)

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

# 默认路径
BASE_DIR = Path(__file__).parent.resolve()
DEFAULT_CSV = BASE_DIR / "02_summary_csv" / "_all_papers.csv"
DEFAULT_OUTPUT = BASE_DIR / "03_visualizations"


def load_data(csv_path: Path) -> pd.DataFrame:
    """加载CSV数据"""
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV文件不存在: {csv_path}")
    return pd.read_csv(csv_path, encoding='utf-8-sig')


def generate_wordcloud(df: pd.DataFrame, output_path: Path):
    """
    生成关键词词云

    Args:
        df: 包含'关键词'列的DataFrame
        output_path: 输出图片路径
    """
    try:
        from wordcloud import WordCloud
    except ImportError:
        print("   [!] 词云功能需要安装wordcloud库: pip install wordcloud")
        return

    print("   生成关键词词云...")

    # 提取关键词
    if '关键词' not in df.columns:
        print("   [!] CSV中没有'关键词'列，跳过词云生成")
        return

    keywords_series = df['关键词'].dropna()
    if keywords_series.empty:
        print("   [!] 关键词列为空，跳过词云生成")
        return

    # 拆分关键词并统计频率
    all_keywords = []
    for kw_str in keywords_series:
        keywords = [k.strip() for k in str(kw_str).split(',') if k.strip()]
        all_keywords.extend(keywords)

    if not all_keywords:
        print("   [!] 没有有效关键词，跳过词云生成")
        return

    word_freq = pd.Series(all_keywords).value_counts().to_dict()

    # 尝试使用中文字体
    font_path = None
    possible_fonts = [
        'C:/Windows/Fonts/simhei.ttf',
        'C:/Windows/Fonts/msyh.ttc',
        '/System/Library/Fonts/PingFang.ttc',
        '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
    ]
    for fp in possible_fonts:
        if Path(fp).exists():
            font_path = fp
            break

    wc = WordCloud(
        font_path=font_path,
        width=1200,
        height=600,
        background_color='white',
        max_words=100,
        colormap='viridis',
    )
    wc.generate_from_frequencies(word_freq)

    plt.figure(figsize=(12, 6))
    plt.imshow(wc, interpolation='bilinear')
    plt.axis('off')
    plt.title('Keywords Word Cloud', fontsize=16, pad=10)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()

    print(f"   [OK] 词云已保存: {output_path}")


def generate_paper_type_distribution(df: pd.DataFrame, output_path: Path):
    """
    生成论文类型分布饼图

    Args:
        df: 包含'论文类型'列的DataFrame
        output_path: 输出图片路径
    """
    print("   生成论文类型分布图...")

    if '论文类型' not in df.columns:
        print("   [!] CSV中没有'论文类型'列，跳过分布图生成")
        return

    type_counts = df['论文类型'].dropna().value_counts()
    if type_counts.empty:
        print("   [!] 论文类型列为空，跳过分布图生成")
        return

    # 颜色方案
    colors = plt.cm.Set3(range(len(type_counts)))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    # 饼图
    wedges, texts, autotexts = ax1.pie(
        type_counts.values,
        labels=type_counts.index,
        autopct='%1.1f%%',
        colors=colors,
        startangle=90,
        pctdistance=0.75,
    )
    ax1.set_title('Paper Type Distribution (Pie)', fontsize=14)

    # 柱状图
    bars = ax2.bar(type_counts.index, type_counts.values, color=colors)
    ax2.set_xlabel('Paper Type', fontsize=12)
    ax2.set_ylabel('Count', fontsize=12)
    ax2.set_title('Paper Type Distribution (Bar)', fontsize=14)
    ax2.tick_params(axis='x', rotation=30)

    # 在柱子上显示数值
    for bar, count in zip(bars, type_counts.values):
        ax2.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.3,
            str(count),
            ha='center',
            va='bottom',
            fontsize=10
        )

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()

    print(f"   [OK] 分布图已保存: {output_path}")


def generate_year_theme_heatmap(df: pd.DataFrame, output_path: Path):
    """
    生成年份-主题热力图

    Args:
        df: 包含'年份'和'领域标签'列的DataFrame
        output_path: 输出图片路径
    """
    try:
        import seaborn as sns
    except ImportError:
        print("   [!] 热力图功能需要安装seaborn库: pip install seaborn")
        return

    print("   生成年份-主题热力图...")

    if '年份' not in df.columns or '领域标签' not in df.columns:
        print("   [!] CSV中缺少'年份'或'领域标签'列，跳过热力图生成")
        return

    # 提取主题桶（领域标签的第一个元素）
    def extract_theme_bucket(tags_str):
        if pd.isna(tags_str):
            return None
        tags = str(tags_str).split(',')
        return tags[0].strip() if tags else None

    df_copy = df.copy()
    df_copy['主题桶'] = df_copy['领域标签'].apply(extract_theme_bucket)
    df_copy = df_copy.dropna(subset=['年份', '主题桶'])

    if df_copy.empty:
        print("   [!] 没有有效的年份-主题数据，跳过热力图生成")
        return

    # 确保年份是整数
    df_copy['年份'] = df_copy['年份'].astype(int)

    # 创建交叉表
    pivot = pd.crosstab(df_copy['主题桶'], df_copy['年份'])

    if pivot.empty:
        print("   [!] 交叉表为空，跳过热力图生成")
        return

    # 计算图表大小
    n_themes = len(pivot.index)
    n_years = len(pivot.columns)
    fig_width = max(10, n_years * 0.8)
    fig_height = max(6, n_themes * 0.6)

    plt.figure(figsize=(fig_width, fig_height))
    sns.heatmap(
        pivot,
        annot=True,
        fmt='d',
        cmap='YlOrRd',
        linewidths=0.5,
        cbar_kws={'label': 'Paper Count'}
    )
    plt.title('Year-Theme Distribution Heatmap', fontsize=14, pad=15)
    plt.xlabel('Year', fontsize=12)
    plt.ylabel('Theme Bucket', fontsize=12)
    plt.xticks(rotation=45)
    plt.yticks(rotation=0)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()

    print(f"   [OK] 热力图已保存: {output_path}")


def main():
    parser = argparse.ArgumentParser(description='Generate visualizations from paper CSV data')
    parser.add_argument(
        '--input', '-i',
        type=str,
        default=str(DEFAULT_CSV),
        help=f'Input CSV file path (default: {DEFAULT_CSV})'
    )
    parser.add_argument(
        '--output', '-o',
        type=str,
        default=str(DEFAULT_OUTPUT),
        help=f'Output directory for visualizations (default: {DEFAULT_OUTPUT})'
    )
    args = parser.parse_args()

    csv_path = Path(args.input)
    output_dir = Path(args.output)

    print("=" * 60)
    print("  Paper Data Visualization Tool")
    print("=" * 60)
    print(f"\n   Input:  {csv_path}")
    print(f"   Output: {output_dir}")

    # 加载数据
    try:
        df = load_data(csv_path)
        print(f"\n   Loaded {len(df)} papers")
    except FileNotFoundError as e:
        print(f"\n   [ERROR] {e}")
        return

    # 创建输出目录
    output_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "-" * 60)
    print("  Generating visualizations...")
    print("-" * 60 + "\n")

    # 生成可视化
    generate_wordcloud(df, output_dir / "01_keywords_wordcloud.png")
    generate_paper_type_distribution(df, output_dir / "02_paper_type_distribution.png")
    generate_year_theme_heatmap(df, output_dir / "03_year_theme_heatmap.png")

    print("\n" + "=" * 60)
    print("  All visualizations completed!")
    print(f"  Output directory: {output_dir}")
    print("=" * 60)


if __name__ == "__main__":
    main()
