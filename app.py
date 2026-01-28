#!/usr/bin/env python3
"""
Git Archive WebUI - ��库克隆和管理系统
"""

import os
import subprocess
import re
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import logging

# 配置
BASE_DIR = "/root/gitarchive"
MIRROR_PREFIX = "bgithub.xyz"
DEFAULT_MIRROR = "github.com"

app = Flask(__name__)
app.secret_key = "your-secret-key-change-this"

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('gitarchive.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 全局调度器
scheduler = None


def get_current_month_path():
    """获取当前年月路径"""
    now = datetime.now()
    return os.path.join(BASE_DIR, str(now.year), f"{now.month:02d}")


def ensure_directory_exists(path):
    """确保目录存在"""
    os.makedirs(path, exist_ok=True)


def parse_github_url(url):
    """解析GitHub URL获取仓库信息（支持镜像地址）"""
    # 支持多种URL格式，包括镜像地址
    patterns = [
        # HTTPS 格式（支持任何域名）
        r'https?://[^/]+/([^/]+)/([^/]+?)(?:\.git)?/?',
        # SSH 格式
        r'git@[^/]+:([^/]+)/([^/]+?)(?:\.git)?/?',
    ]

    for pattern in patterns:
        match = re.match(pattern, url)
        if match:
            owner, repo = match.groups()
            # 去除 .git 后缀
            if repo.endswith('.git'):
                repo = repo[:-4]
            return owner, repo

    return None, None


def apply_mirror(url, mirror_prefix=MIRROR_PREFIX):
    """应用镜像地址"""
    if not mirror_prefix:
        return url
    return url.replace(DEFAULT_MIRROR, mirror_prefix)


def clone_repository(url, mirror_prefix=MIRROR_PREFIX):
    """克隆仓库到指定目录"""
    owner, repo = parse_github_url(url)
    if not owner or not repo:
        return {"success": False, "error": "无效的GitHub仓库地址"}

    target_dir = get_current_month_path()
    ensure_directory_exists(target_dir)

    repo_path = os.path.join(target_dir, repo)
    if os.path.exists(repo_path):
        return {"success": False, "error": f"仓库 {repo} 已存在"}

    # 应用镜像
    clone_url = apply_mirror(url, mirror_prefix)

    try:
        logger.info(f"开始克隆: {clone_url}")
        result = subprocess.run(
            ["git", "clone", clone_url, repo_path],
            capture_output=True,
            text=True,
            timeout=300  # 5分钟超时
        )

        if result.returncode == 0:
            logger.info(f"克隆成功: {repo}")
            return {"success": True, "repo": repo, "path": repo_path}
        else:
            logger.error(f"克隆失败: {result.stderr}")
            return {"success": False, "error": result.stderr}

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "克隆超时"}
    except Exception as e:
        logger.error(f"克隆异常: {str(e)}")
        return {"success": False, "error": str(e)}


def get_repositories_by_path(path):
    """获取指定路径下的所有仓库"""
    if not os.path.exists(path):
        return []

    repos = []
    for item in os.listdir(path):
        item_path = os.path.join(path, item)
        if os.path.isdir(item_path):
            # 检查是否是git仓库
            git_dir = os.path.join(item_path, ".git")
            if os.path.exists(git_dir):
                # 获取仓库信息
                repos.append({
                    "name": item,
                    "path": item_path,
                    "last_update": get_last_commit_date(item_path)
                })

    return sorted(repos, key=lambda x: x["name"])


def get_last_commit_date(repo_path):
    """获取仓库最后一次提交日期"""
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "log", "-1", "--format=%ci"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split()[0]
    except:
        pass
    return "未知"


def update_repository(repo_path, mirror_prefix=MIRROR_PREFIX):
    """更新仓库"""
    try:
        # 获取远程URL
        result = subprocess.run(
            ["git", "-C", repo_path, "remote", "-v"],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0:
            # 提取远程URL并应用镜像
            for line in result.stdout.split('\n'):
                if 'origin' in line and 'fetch' in line:
                    url = line.split()[1]
                    mirror_url = apply_mirror(url, mirror_prefix)

                    # 更新远程URL
                    subprocess.run(
                        ["git", "-C", repo_path, "remote", "set-url", "origin", mirror_url],
                        capture_output=True,
                        timeout=5
                    )
                    break

        # 执行更新
        result = subprocess.run(
            ["git", "-C", repo_path, "pull", "origin"],
            capture_output=True,
            text=True,
            timeout=300
        )

        if result.returncode == 0:
            logger.info(f"更新成功: {repo_path}")
            return {"success": True, "path": repo_path}
        else:
            logger.error(f"更新失败: {result.stderr}")
            return {"success": False, "error": result.stderr}

    except Exception as e:
        logger.error(f"更新异常: {str(e)}")
        return {"success": False, "error": str(e)}


def update_all_repositories(mirror_prefix=MIRROR_PREFIX):
    """更新所有仓库"""
    logger.info("开始批量更新所有仓库")

    if not os.path.exists(BASE_DIR):
        logger.warning(f"基础目录不存在: {BASE_DIR}")
        return

    updated_count = 0
    failed_count = 0

    for root, dirs, files in os.walk(BASE_DIR):
        for dir_name in dirs:
            repo_path = os.path.join(root, dir_name)
            git_dir = os.path.join(repo_path, ".git")

            if os.path.exists(git_dir):
                result = update_repository(repo_path, mirror_prefix)
                if result["success"]:
                    updated_count += 1
                else:
                    failed_count += 1

    logger.info(f"批量更新完成: 成功 {updated_count}, 失败 {failed_count}")


# ===== Flask 路由 =====

@app.route('/')
def index():
    """主页 - 克隆仓库"""
    now = datetime.now()
    return render_template('index.html', mirror_prefix=MIRROR_PREFIX, now=now)


@app.route('/clone', methods=['POST'])
def clone():
    """处理克隆请求"""
    url = request.form.get('url', '').strip()
    mirror_prefix = request.form.get('mirror_prefix', MIRROR_PREFIX).strip()

    if not url:
        flash('请输入仓库地址', 'error')
        return redirect(url_for('index'))

    result = clone_repository(url, mirror_prefix)

    if result['success']:
        flash(f'成功克隆仓库: {result["repo"]}', 'success')
    else:
        flash(f'克隆失败: {result["error"]}', 'error')

    return redirect(url_for('index'))


@app.route('/repositories')
def repositories():
    """仓库列表页面"""
    year = request.args.get('year', datetime.now().year)
    month = request.args.get('month', f"{datetime.now().month:02d}")

    target_path = os.path.join(BASE_DIR, str(year), month)
    repos = get_repositories_by_path(target_path)

    # 获取所有可用的年月（只显示数字格式的年月目录）
    available_periods = []
    if os.path.exists(BASE_DIR):
        for year_dir in sorted(os.listdir(BASE_DIR), reverse=True):
            # 只处理 4 位数字的年份目录
            if not year_dir.isdigit() or len(year_dir) != 4:
                continue
            year_path = os.path.join(BASE_DIR, year_dir)
            if os.path.isdir(year_path):
                for month_dir in sorted(os.listdir(year_path), reverse=True):
                    # 只处理 2 位数字的月份目录
                    if not month_dir.isdigit() or len(month_dir) != 2:
                        continue
                    month_path = os.path.join(year_path, month_dir)
                    if os.path.isdir(month_path):
                        available_periods.append({
                            'year': year_dir,
                            'month': month_dir,
                            'label': f'{year_dir}/{month_dir}'
                        })

    return render_template(
        'repositories.html',
        repos=repos,
        current_year=str(year),
        current_month=month,
        available_periods=available_periods,
        current_period=f"{year}/{month}"
    )


@app.route('/update/<path:repo_path>')
def update_repo(repo_path):
    """更新单个仓库"""
    # 安全检查：确保路径在BASE_DIR下
    if not os.path.abspath(repo_path).startswith(BASE_DIR):
        return jsonify({"success": False, "error": "非法路径"})

    mirror_prefix = request.args.get('mirror_prefix', MIRROR_PREFIX)
    result = update_repository(repo_path, mirror_prefix)
    return jsonify(result)


@app.route('/api/stats')
def stats():
    """获取统计信息"""
    total_repos = 0
    total_size = 0

    if os.path.exists(BASE_DIR):
        for root, dirs, files in os.walk(BASE_DIR):
            for dir_name in dirs:
                repo_path = os.path.join(root, dir_name)
                git_dir = os.path.join(repo_path, ".git")
                if os.path.exists(git_dir):
                    total_repos += 1
                    # 计算大小
                    for dirpath, dirnames, filenames in os.walk(repo_path):
                        for filename in filenames:
                            filepath = os.path.join(dirpath, filename)
                            if os.path.exists(filepath):
                                total_size += os.path.getsize(filepath)

    # 转换为MB
    total_size_mb = total_size / (1024 * 1024)

    return jsonify({
        "total_repos": total_repos,
        "total_size_mb": round(total_size_mb, 2)
    })


def start_scheduler():
    """启动定时任务"""
    global scheduler
    scheduler = BackgroundScheduler()

    # 每周日凌晨2点执行更新
    scheduler.add_job(
        func=update_all_repositories,
        trigger=CronTrigger(day_of_week='sun', hour=2, minute=0),
        id='weekly_update',
        name='每周更新所有仓库',
        args=[MIRROR_PREFIX]
    )

    scheduler.start()
    logger.info("定时任务已启动：每周日 02:00 更新所有仓库")


if __name__ == '__main__':
    # 确保基础目录存在
    ensure_directory_exists(BASE_DIR)

    # 启动定时任务
    start_scheduler()

    # 启动Web服务
    app.run(host='0.0.0.0', port=5000, debug=True)
