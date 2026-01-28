# Git Archive WebUI

GitHub 仓库克隆和自动更新管理系统

## 功能特性

- **克隆仓库**：通过 Web 界面快速克隆 GitHub 仓库
- **自动分类**：按年月自动组织仓库（如 `/root/gitarchive/2026/01/`）
- **镜像支持**：支持自定义镜像地址（如 `bgithub.xyz`），加速克隆和更新
- **仓库管理**：浏览、查看、更新已克隆的仓库
- **自动更新**：每周自动更新所有仓库，每两个仓库间隔一分钟
- **统计信息**：实时显示仓库总数和占用空间

## 安装

### 1. 克隆项目

```bash
cd /root/gitarchive
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置

编辑 `app.py` 修改配置：

```python
BASE_DIR = "/root/gitarchive"  # 仓库��存根目录
MIRROR_PREFIX = "bgithub.xyz"  # 默认镜像前缀
```

### 4. 运行

```bash
python app.py
```

访问：`http://localhost:5000`

## 使用说明

### 克隆仓库

1. 在首页输入 GitHub 仓库地址
2. （可选）填写镜像地址前缀，如 `bgithub.xyz`
3. 点击"开始克隆"
4. 仓库将被保存到 `/root/gitarchive/YYYY/MM/` 目录

### 查看仓库

1. 点击顶部"仓库列表"
2. 选择年月查看该月份克隆的仓库
3. 可以单独更新每个仓库

### 自动更新

系统默认配置：
- **更新时间**：每周日凌晨 2:00
- **更新方式**：使用镜像地址批量更新
- **更新间隔**：每两个仓库间隔 1 分钟

可在 `app.py` 中修改定时任务配置：

```python
scheduler.add_job(
    func=update_all_repositories,
    trigger=CronTrigger(day_of_week='sun', hour=2, minute=0),
    id='weekly_update',
    name='每周更新所有仓库',
    args=[MIRROR_PREFIX]
)
```

## 支持的 URL 格式

- `https://github.com/username/repo`
- `https://github.com/username/repo.git`
- `git@github.com:username/repo.git`

## 目录结构

```
gitarchive/
├── app.py                 # 主应用
├── requirements.txt       # 依赖列表
├── templates/             # HTML 模板
│   ├── index.html        # 克隆页面
│   └── repositories.html # 仓库列表页面
├── static/               # 静态资源
│   └── css/
│       └── style.css     # 样式文件
└── gitarchive.log        # 应用日志
```

## 技术栈

- **后端**：Flask
- **定时任务**：APScheduler
- **版本控制**：Git
- **前端**：原生 HTML/CSS/JavaScript

## 注意事项

1. 确保 `/root/gitarchive` 目录有写入权限
2. 确保系统已安装 Git
3. 镜像地址只在克隆和更新时生效
4. 定时任务日志会写入 `gitarchive.log`

## 许可证

MIT License
