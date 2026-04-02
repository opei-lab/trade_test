# Git Rules

## Pre-execution Verification

- Before any destructive/irreversible bash command: state what it does and get approval
- Before git operations: run `git status` to confirm current state
- Before push: verify branch name and remote target

## Core Principles

- 履歴を壊さない。`--force`、`--force-with-lease`、`reset --hard`は原則禁止
- 履歴は1直線に保つ。mergeよりrebaseを優先する
- 操作前に最新を取り込む。push/rebase前には必ずfetchする

## ブランチ運用

### 作業開始時

```bash
git fetch origin
git checkout -b feature/xxx origin/main
```

### 作業中の最新取り込み

```bash
git fetch origin
git rebase origin/main
```

- IMPORTANT: `git pull`は使わない。`fetch` + `rebase`で明示的に行う
- rebaseコンフリクト発生時は、自動解決せずユーザーに報告する

### コミット

- コミットはユーザーの明示的な指示がある場合のみ行う
- コミットメッセージは「なぜ」に焦点を当てる
- 1コミット = 1つの論理的変更単位
- `--amend`は原則使わない。新しいコミットを作る
- IMPORTANT: `Co-Authored-By`やAI生成を示す文言をコミットメッセージに含めない

### Push前

```bash
git fetch origin
git rebase origin/main  # コンフリクトがないことを確認
git push -u origin <branch>
```

- IMPORTANT: `git push --force`は絶対に使わない
- push先は常に作業ブランチ。mainへの直接pushは禁止

## 禁止操作（ユーザーの明示的指示がない限り）

- `git push --force` / `--force-with-lease`
- `git reset --hard`
- `git checkout .` / `git restore .`（作業内容の破棄）
- `git clean -f`
- `git branch -D`（大文字D：強制削除）
- `git rebase -i`（インタラクティブrebaseはCLI非対応）
- mainブランチへの直接push

## コンフリクト対応

1. コンフリクト発生時は、まずユーザーに報告する
2. コンフリクト箇所と両方の変更内容を説明する
3. 解決案を提示し、ユーザーの承認を得てから解決する
4. 自動マージや推測での解決は行わない
