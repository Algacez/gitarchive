#!/usr/bin/env python3
"""
Git Archive WebUI - 仓库克隆和管理系统
"""

import json
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask, flash, jsonify, redirect, render_template, request, send_file, url_for

# 配置
BASE_DIR = "/root/gitarchive"
BASE_DIR_ABS = os.path.abspath(BASE_DIR)
DEFAULT_PROXY_PREFIX = "bgithub.xyz"
DEFAULT_MIRROR = "github.com"
SETTINGS_FILE = os.path.join(BASE_DIR, ".gitarchive_settings.json")

app = Flask(__name__)
app.secret_key = "your-secret-key-change-this"

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("gitarchive.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# 全局调度器
scheduler = None


def ensure_directory_exists(path):
    """确保目录存在"""
    os.makedirs(path, exist_ok=True)


def get_default_settings():
    """默认配置"""
    return {
        "proxy": {
            "enabled": True,
            "mirror_prefix": DEFAULT_PROXY_PREFIX,
        },
        "repo_settings": {},
    }


def load_settings():
    """加载配置文件"""
    ensure_directory_exists(BASE_DIR)
    default_settings = get_default_settings()

    if not os.path.exists(SETTINGS_FILE):
        return default_settings

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"读取配置失败: {e}")
        return default_settings

    proxy = data.get("proxy", {})
    mirror_prefix = str(proxy.get("mirror_prefix", DEFAULT_PROXY_PREFIX)).strip() or DEFAULT_PROXY_PREFIX
    enabled = bool(proxy.get("enabled", True))

    repo_settings = data.get("repo_settings", {})
    if not isinstance(repo_settings, dict):
        repo_settings = {}

    return {
        "proxy": {
            "enabled": enabled,
            "mirror_prefix": mirror_prefix,
        },
        "repo_settings": repo_settings,
    }


def save_settings(settings):
    """保存配置文件"""
    ensure_directory_exists(BASE_DIR)
    tmp_file = f"{SETTINGS_FILE}.tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, SETTINGS_FILE)


def get_proxy_settings():
    """获取代理设置"""
    settings = load_settings()
    return settings["proxy"]


def save_proxy_settings(enabled, mirror_prefix):
    """持久化代理设置"""
    settings = load_settings()
    safe_prefix = (mirror_prefix or DEFAULT_PROXY_PREFIX).strip() or DEFAULT_PROXY_PREFIX
    settings["proxy"] = {
        "enabled": bool(enabled),
        "mirror_prefix": safe_prefix,
    }
    save_settings(settings)


def get_effective_mirror_prefix():
    """获取当前生效的镜像前缀"""
    proxy = get_proxy_settings()
    return proxy["mirror_prefix"] if proxy["enabled"] else DEFAULT_MIRROR


def get_repo_weekly_update(repo_path, settings=None):
    """获取仓库每周更新开关"""
    data = settings if settings is not None else load_settings()
    repo_cfg = data.get("repo_settings", {}).get(repo_path, {})
    return bool(repo_cfg.get("weekly_update", True))


def set_repo_weekly_update(repo_path, weekly_update):
    """设置仓库每周更新开关"""
    settings = load_settings()
    repo_cfg = settings.setdefault("repo_settings", {}).setdefault(repo_path, {})
    repo_cfg["weekly_update"] = bool(weekly_update)
    save_settings(settings)


def remove_repo_setting(repo_path):
    """移除仓库配置"""
    settings = load_settings()
    repo_settings = settings.get("repo_settings", {})
    if repo_path in repo_settings:
        repo_settings.pop(repo_path, None)
        save_settings(settings)


def get_current_month_path():
    """获取当前年月路径"""
    now = datetime.now()
    return os.path.join(BASE_DIR, str(now.year), f"{now.month:02d}")


def normalize_absolute_path(raw_path):
    """将路径标准化为绝对路径"""
    if not os.path.isabs(raw_path):
        raw_path = "/" + raw_path
    return os.path.abspath(raw_path)


def is_safe_path(path):
    """检查路径是否位于 BASE_DIR 下"""
    abs_path = os.path.abspath(path)
    return abs_path == BASE_DIR_ABS or abs_path.startswith(BASE_DIR_ABS + os.sep)


def validate_repo_path(raw_path):
    """校验仓库路径"""
    repo_path = normalize_absolute_path(raw_path)
    if not is_safe_path(repo_path):
        return None, "非法路径"
    if not os.path.isdir(repo_path):
        return None, "仓库不存在"
    if not os.path.exists(os.path.join(repo_path, ".git")):
        return None, "不是有效的Git仓库"
    return repo_path, None


def get_repo_archive_path(repo_path):
    """获取仓库压缩包路径"""
    repo_name = os.path.basename(repo_path)
    return os.path.join(os.path.dirname(repo_path), f"{repo_name}.zip")


def get_available_periods():
    """获取可用归档月份"""
    periods = []
    if not os.path.exists(BASE_DIR):
        return periods

    for year_dir in sorted(os.listdir(BASE_DIR), reverse=True):
        if not year_dir.isdigit() or len(year_dir) != 4:
            continue
        year_path = os.path.join(BASE_DIR, year_dir)
        if not os.path.isdir(year_path):
            continue
        for month_dir in sorted(os.listdir(year_path), reverse=True):
            if not month_dir.isdigit() or len(month_dir) != 2:
                continue
            month_path = os.path.join(year_path, month_dir)
            if os.path.isdir(month_path):
                periods.append(
                    {
                        "year": year_dir,
                        "month": month_dir,
                        "label": f"{year_dir}/{month_dir}",
                    }
                )
    return periods


def iter_archived_repositories():
    """遍历所有归档仓库路径"""
    for period in get_available_periods():
        month_path = os.path.join(BASE_DIR, period["year"], period["month"])
        for item in os.listdir(month_path):
            item_path = os.path.join(month_path, item)
            git_dir = os.path.join(item_path, ".git")
            if os.path.isdir(item_path) and os.path.exists(git_dir):
                yield item_path


def parse_github_url(url):
    """解析GitHub URL获取仓库信息（支持镜像地址）"""
    patterns = [
        r"https?://[^/]+/([^/]+)/([^/]+)/?",
        r"git@[^/]+:([^/]+)/([^/]+)/?",
    ]

    for pattern in patterns:
        match = re.match(pattern, url)
        if match:
            owner, repo = match.groups()
            if repo.endswith(".git"):
                repo = repo[:-4]
            return owner, repo

    return None, None


def rewrite_git_host(url, host):
    """替换 Git URL 的域名"""
    https_match = re.match(r"^(https?://)([^/]+)(/.*)$", url)
    if https_match:
        return f"{https_match.group(1)}{host}{https_match.group(3)}"

    ssh_match = re.match(r"^(git@)([^:]+)(:.*)$", url)
    if ssh_match:
        return f"{ssh_match.group(1)}{host}{ssh_match.group(3)}"

    return url


def apply_mirror(url, mirror_prefix=None):
    """应用代理，关闭代理时强制使用 github.com"""
    target_host = (mirror_prefix or "").strip() or DEFAULT_MIRROR
    return rewrite_git_host(url, target_host)


def clone_repository(url, mirror_prefix=None):
    """克隆仓库到指定目录"""
    owner, repo = parse_github_url(url)
    if not owner or not repo:
        return {"success": False, "error": "无效的GitHub仓库地址"}

    target_dir = get_current_month_path()
    ensure_directory_exists(target_dir)

    repo_path = os.path.join(target_dir, repo)
    if os.path.exists(repo_path):
        return {"success": False, "error": f"仓库 {repo} 已存在"}

    clone_url = apply_mirror(url, mirror_prefix)

    try:
        logger.info(f"开始克隆: {clone_url}")
        result = subprocess.run(
            ["git", "clone", clone_url, repo_path],
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode == 0:
            logger.info(f"克隆成功: {repo}")
            return {"success": True, "repo": repo, "path": repo_path}

        logger.error(f"克隆失败: {result.stderr}")
        return {"success": False, "error": result.stderr}

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "克隆超时"}
    except Exception as e:
        logger.error(f"克隆异常: {str(e)}")
        return {"success": False, "error": str(e)}


def get_repositories_by_path(path, settings=None):
    """获取指定路径下的所有仓库"""
    if not os.path.exists(path):
        return []

    cfg = settings if settings is not None else load_settings()

    repos = []
    for item in os.listdir(path):
        item_path = os.path.join(path, item)
        if not os.path.isdir(item_path):
            continue
        if not os.path.exists(os.path.join(item_path, ".git")):
            continue

        archive_path = get_repo_archive_path(item_path)
        repos.append(
            {
                "name": item,
                "path": item_path,
                "last_update": get_last_commit_date(item_path),
                "url": get_repo_url(item_path),
                "weekly_update": get_repo_weekly_update(item_path, cfg),
                "has_archive": os.path.exists(archive_path),
                "archive_name": os.path.basename(archive_path),
            }
        )

    return sorted(repos, key=lambda x: x["name"])


def get_repo_url(repo_path):
    """获取仓库的远程 URL"""
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return "未知"


def get_last_commit_date(repo_path):
    """获取仓库最后一次提交日期"""
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "log", "-1", "--format=%ci"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split()[0]
    except Exception:
        pass
    return "未知"


def update_repository(repo_path, mirror_prefix=None):
    """更新仓库"""
    try:
        # 获取当前远程URL
        result = subprocess.run(
            ["git", "-C", repo_path, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return {"success": False, "error": "获取远程地址失败"}

        origin_url = result.stdout.strip()
        target_url = apply_mirror(origin_url, mirror_prefix)

        # 同步远程URL以匹配当前代理设置
        set_result = subprocess.run(
            ["git", "-C", repo_path, "remote", "set-url", "origin", target_url],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if set_result.returncode != 0:
            return {"success": False, "error": set_result.stderr.strip() or "设置远程地址失败"}

        pull_result = subprocess.run(
            ["git", "-C", repo_path, "pull", "origin"],
            capture_output=True,
            text=True,
            timeout=300,
        )

        if pull_result.returncode == 0:
            logger.info(f"更新成功: {repo_path}")
            return {"success": True, "path": repo_path}

        logger.error(f"更新失败: {pull_result.stderr}")
        return {"success": False, "error": pull_result.stderr}

    except Exception as e:
        logger.error(f"更新异常: {str(e)}")
        return {"success": False, "error": str(e)}


def update_all_repositories():
    """按仓库开关批量更新仓库"""
    logger.info("开始批量更新仓库")

    if not os.path.exists(BASE_DIR):
        logger.warning(f"基础目录不存在: {BASE_DIR}")
        return

    settings = load_settings()
    mirror_prefix = settings["proxy"]["mirror_prefix"] if settings["proxy"]["enabled"] else DEFAULT_MIRROR

    updated_count = 0
    failed_count = 0
    skipped_count = 0

    for repo_path in iter_archived_repositories():
        if not get_repo_weekly_update(repo_path, settings):
            skipped_count += 1
            continue

        result = update_repository(repo_path, mirror_prefix)
        if result["success"]:
            updated_count += 1
        else:
            failed_count += 1

    logger.info(f"批量更新完成: 成功 {updated_count}, 失败 {failed_count}, 跳过 {skipped_count}")


def create_repository_archive(repo_path):
    """创建仓库压缩包（仓库名.zip）"""
    archive_path = get_repo_archive_path(repo_path)
    archive_base = archive_path[:-4]
    if os.path.exists(archive_path):
        os.remove(archive_path)

    shutil.make_archive(
        archive_base,
        "zip",
        root_dir=os.path.dirname(repo_path),
        base_dir=os.path.basename(repo_path),
    )
    return archive_path


def delete_repository(repo_path):
    """删除仓库，同时删除压缩包和配置"""
    archive_path = get_repo_archive_path(repo_path)
    shutil.rmtree(repo_path)
    if os.path.exists(archive_path):
        os.remove(archive_path)
    remove_repo_setting(repo_path)


# ===== Flask 路由 =====


@app.route("/")
def index():
    """主页 - 克隆仓库"""
    now = datetime.now()
    proxy_settings = get_proxy_settings()
    return render_template("index.html", proxy_settings=proxy_settings, now=now)


@app.route("/clone", methods=["POST"])
def clone():
    """处理克隆请求"""
    url = request.form.get("url", "").strip()
    use_proxy = request.form.get("use_proxy", "1") == "1"
    mirror_prefix = request.form.get("mirror_prefix", DEFAULT_PROXY_PREFIX).strip() or DEFAULT_PROXY_PREFIX
    weekly_update = request.form.get("weekly_update", "on") == "on"

    if not url:
        flash("请输入仓库地址", "error")
        return redirect(url_for("index"))

    # 持久化代理设置
    save_proxy_settings(use_proxy, mirror_prefix)
    effective_mirror = mirror_prefix if use_proxy else DEFAULT_MIRROR

    result = clone_repository(url, effective_mirror)

    if result["success"]:
        set_repo_weekly_update(result["path"], weekly_update)
        weekly_text = "已开启" if weekly_update else "已关闭"
        flash(f'成功克隆仓库: {result["repo"]}（每周更新{weekly_text}）', "success")
    else:
        flash(f'克隆失败: {result["error"]}', "error")

    return redirect(url_for("index"))


@app.route("/repositories")
def repositories():
    """仓库列表页面"""
    year = str(request.args.get("year", datetime.now().year))
    month = str(request.args.get("month", f"{datetime.now().month:02d}"))
    settings = load_settings()

    available_periods = get_available_periods()
    months_by_year = {}
    for period in available_periods:
        months_by_year.setdefault(period["year"], []).append(period["month"])

    available_years = sorted(months_by_year.keys(), reverse=True)
    if available_years and year not in months_by_year:
        year = available_years[0]
    if months_by_year.get(year) and month not in months_by_year[year]:
        month = months_by_year[year][0]

    target_path = os.path.join(BASE_DIR, year, month)
    repos = get_repositories_by_path(target_path, settings)

    return render_template(
        "repositories.html",
        repos=repos,
        current_year=year,
        current_month=month,
        available_periods=available_periods,
        available_years=available_years,
        months_by_year=months_by_year,
        current_period=f"{year}/{month}",
        proxy_settings=settings["proxy"],
    )


@app.route("/update/<path:repo_path>", methods=["GET"])
def update_repo_legacy(repo_path):
    """兼容旧接口：更新单个仓库"""
    safe_repo_path, error = validate_repo_path(repo_path)
    if error:
        return jsonify({"success": False, "error": error})

    mirror_prefix = request.args.get("mirror_prefix")
    if mirror_prefix is None:
        mirror_prefix = get_effective_mirror_prefix()
    result = update_repository(safe_repo_path, mirror_prefix)
    return jsonify(result)


@app.route("/api/repository/update", methods=["POST"])
def update_repo():
    """更新单个仓库"""
    data = request.get_json(silent=True) or {}
    safe_repo_path, error = validate_repo_path(data.get("repo_path", ""))
    if error:
        return jsonify({"success": False, "error": error})

    mirror_prefix = get_effective_mirror_prefix()
    result = update_repository(safe_repo_path, mirror_prefix)
    return jsonify(result)


@app.route("/api/repository/weekly-update", methods=["POST"])
def set_weekly_update():
    """设置仓库每周更新开关"""
    data = request.get_json(silent=True) or {}
    safe_repo_path, error = validate_repo_path(data.get("repo_path", ""))
    if error:
        return jsonify({"success": False, "error": error})

    weekly_update = bool(data.get("weekly_update", True))
    set_repo_weekly_update(safe_repo_path, weekly_update)
    return jsonify({"success": True, "weekly_update": weekly_update})


@app.route("/api/settings/proxy", methods=["POST"])
def save_proxy():
    """保存代理设置"""
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled", True))
    mirror_prefix = str(data.get("mirror_prefix", DEFAULT_PROXY_PREFIX)).strip() or DEFAULT_PROXY_PREFIX
    save_proxy_settings(enabled, mirror_prefix)
    return jsonify({"success": True})


@app.route("/api/repository/archive", methods=["POST"])
def archive_repository():
    """打包仓库并返回下载链接"""
    data = request.get_json(silent=True) or {}
    safe_repo_path, error = validate_repo_path(data.get("repo_path", ""))
    if error:
        return jsonify({"success": False, "error": error})

    try:
        archive_path = create_repository_archive(safe_repo_path)
        download_url = url_for("download_archive", archive_path=archive_path.lstrip("/"))
        return jsonify(
            {
                "success": True,
                "download_url": download_url,
                "archive_name": os.path.basename(archive_path),
            }
        )
    except Exception as e:
        logger.error(f"打包失败: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route("/download/<path:archive_path>")
def download_archive(archive_path):
    """下载压缩包"""
    safe_archive_path = normalize_absolute_path(archive_path)
    if not is_safe_path(safe_archive_path):
        return "非法路径", 403
    if not safe_archive_path.endswith(".zip"):
        return "文件类型不支持", 400
    if not os.path.exists(safe_archive_path):
        return "压缩包不存在", 404
    return send_file(
        safe_archive_path,
        as_attachment=True,
        download_name=os.path.basename(safe_archive_path),
    )


@app.route("/api/repository/delete", methods=["POST"])
def remove_repository():
    """删除仓库（含压缩包）"""
    data = request.get_json(silent=True) or {}
    safe_repo_path, error = validate_repo_path(data.get("repo_path", ""))
    if error:
        return jsonify({"success": False, "error": error})

    try:
        delete_repository(safe_repo_path)
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"删除仓库失败: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/stats")
def stats():
    """获取统计信息"""
    total_repos = 0
    total_size = 0

    for repo_path in iter_archived_repositories():
        total_repos += 1
        for dirpath, _, filenames in os.walk(repo_path):
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                if os.path.exists(filepath):
                    total_size += os.path.getsize(filepath)

    total_size_mb = total_size / (1024 * 1024)
    return jsonify(
        {
            "total_repos": total_repos,
            "total_size_mb": round(total_size_mb, 2),
        }
    )


def start_scheduler():
    """启动定时任务"""
    global scheduler
    scheduler = BackgroundScheduler()

    scheduler.add_job(
        func=update_all_repositories,
        trigger=CronTrigger(day_of_week="sun", hour=2, minute=0),
        id="weekly_update",
        name="每周更新（仅更新已开启仓库）",
    )

    scheduler.start()
    logger.info("定时任务已启动：每周日 02:00 按仓库开关自动更新")


if __name__ == "__main__":
    ensure_directory_exists(BASE_DIR)
    start_scheduler()
    app.run(host="0.0.0.0", port=5000, debug=True)
