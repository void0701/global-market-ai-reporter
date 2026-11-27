# 13. Phase 2 実行計画と作業分担

最終更新: 2026-11-25 / 状態: **S0 完了**（結果は [06 §8-9](06-cicd-and-environments.md)）

Phase 2（AWS インフラ構築と Lambda 化）の作業を、**誰がやるか**で整理したものです。
設計の詳細は [06](06-cicd-and-environments.md)、コストは [03](03-cost-and-tokens.md) を参照。

---

## 0. 全体像

```
S0 権限の検証スパイク  ← 🔴 ここで止まる可能性がある。最初にやる
   ↓
S1 bootstrap（state置き場 + OIDC + IAMロール）
   ↓
S2 モジュール記述（report_site / pipeline / secrets）
   ↓
S3 lambda_handler.py の実装
   ↓
S4 dev へデプロイ → スモークテスト
   ↓
S5 シークレット投入（prod）
   ↓
S6 prod へデプロイ → 本番稼働
```

**S0 で詰まったら、それ以降は方針判断が必要になります。** 先に進む前に必ず実施してください。

---

## 1. 役割分担（サマリ）

| | ken さん | Claude |
|---|---|---|
| **ツール導入** | **awscli（公式インストーラ）← 必須** | terraform（✅ 導入済み） |
| **AWS 認証** | ローカルから AWS を操作できる状態にする | — |
| **検証スパイク** | `terraform apply` の実行と結果の共有 | スパイク用 HCL の作成 |
| **bootstrap** | `terraform apply`（手動・1回） | HCL の作成 |
| **モジュール記述** | レビュー | 全部 |
| **Lambda ハンドラ** | レビュー | 全部 |
| **シークレット投入** | **AWS CLI から手動で投入** | 手順の提示 |
| **GitHub 設定** | リポジトリ認証 / Environments / ブランチ保護 | ワークフロー YAML の作成 |
| **コスト上限** | AWS Budgets / Claude Console の設定 | 推奨値の提示 |
| **判断** | ~~§4 の 3 件~~ ✅ **全件完了**（2026-11-25） | 選択肢と推奨の提示 |

> **原則: 認証情報に触れる操作と、コンソールでの操作は ken さんが行います。**
> コードと HCL の作成、ローカルでの検証は Claude が行います。

---

## 2. ken さんの作業（チェックリスト）

### 🔴 S0（進行中）

| 作業 | 担当 | 状態 |
|---|---|---|
| terraform の導入 | Claude | ✅ **完了**（v1.17.2） |
| スパイク HCL の作成 | Claude | ✅ **完了**（`infra/spike/` / 17 リソース） |
| `terraform init` / `validate` / `fmt` | Claude | ✅ **完了**（Success） |
| **AWS CLI の導入 + `aws login`** | ken さん | ✅ **完了** |
| Terraform 用の `credential_process` プロファイル | Claude | ✅ **完了**（A-135） |
| `terraform apply` → 結果確認 → `destroy` | Claude | ✅ **完了。16/17 成功** |
| CloudFront の代替検証（Amplify） | Claude | ✅ **完了。採用決定**（A-131） |

#### ken さんの作業: AWS 認証情報

**[確認済: AWS 公式ドキュメント / 2026-11-25]**

> ### ⚠️ 新サインアップ体験では静的なアクセスキーを使いません
>
> 当初「AWS Settings でアクセスキーを発行」と案内しましたが**誤りでした**。
> プロジェクト方式では **`aws login` でブラウザ認証し、一時的なプロファイルを得る**
> 方式です。したがって **AWS CLI が先に必要**です（「S0 に awscli は不要」も誤りでした）。

| 項目 | 内容 |
|---|---|
| 認証コマンド | `aws login --profile <名前>` |
| 有効期間 | **12 時間**（同コマンドで更新） |
| 再認証なしの期間 | 90 日（以降はブラウザで再許可） |
| 形式 | `~/.aws/` のプロファイル。`AWS_ACCESS_KEY_ID` を直接扱わない |

**手順**

1. **AWS CLI を入れる**（公式インストーラ。下記「ツール導入の注意」参照）
2. **AWS Settings**（https://settings.aws.com）→ プロジェクトを選択
   → 右上 **「Create and manage cloud infrastructure」**
   → **「Use your AWS credentials with AI coding tools」**
   → プロファイル名を決める（例: `sandp`）
3. ログイン

```bash
aws login --profile sandp
```

4. 確認

```bash
AWS_PROFILE=sandp aws sts get-caller-identity
```

5. Terraform から使う

```bash
export AWS_PROFILE=sandp
```

> ### ⚠️ Terraform で使えるかは未検証（スパイクの検証項目に追加）
>
> 公式ドキュメントに **「The credentials you generate are for use by the AWS CLI,
> AWS Tools for PowerShell, and AWS SDKs. External tools may not be supported.」**
> という注記があります。Terraform は AWS SDK（Go）を使い標準の認証チェーンを読むため
> **動く見込み**ですが、保証はありません。
>
> **動かない場合の代替:** IAM ユーザーを作成しアクセスキーを発行する
> （IAM は新体験の Free Tier に含まれます）。ただしこの方式が SCP で許可されるかも
> 未検証なので、その場合はスパイクの検証項目が 1 つ増えます。

### ⚠️ ツール導入の注意（2026-11-25 実機確認）

| ツール | コマンド | 注意 |
|---|---|---|
| **terraform** | `brew install hashicorp/tap/terraform` | **`brew install terraform` は失敗します。** HashiCorp のライセンス変更により Homebrew core から外れ、公式 tap に移っています |
| **awscli** | **公式インストーラを推奨**（下記） | **Homebrew 版は避けてください。** `aws-c-*` 群をソースからビルドするため非常に遅く、実測で 45 分経っても終わりませんでした |

```bash
curl -o "/tmp/AWSCLIV2.pkg" "https://awscli.amazonaws.com/AWSCLIV2.pkg"
sudo installer -pkg /tmp/AWSCLIV2.pkg -target /
```

> **awscli は S0 に必須です。** 新サインアップ体験の認証は `aws login`（AWS CLI のコマンド）
> で行うため、これなしには Terraform を動かせません。



> ⚠️ **S0 の結果次第で、以降の計画が変わります。** 拒否された場合は §5 の分岐へ。

### S1 bootstrap（S0 成功後）

- [ ] `infra/bootstrap` で **`terraform apply`**（人間が 1 回だけ実行する設計／[06 §2-4](06-cicd-and-environments.md)）
  - state 用 S3 バケット + KMS キー 2 個
  - GitHub OIDC プロバイダ
  - IAM ロール 3 種（`gha-plan` / `gha-deploy-dev` / `gha-deploy-prod`）
- [ ] 出力された**ロール ARN を共有**（ワークフロー YAML に埋め込みます）

### S4〜S6 デプロイ

- [ ] **GitHub リポジトリの認証を解決**（現在ブロック中／§3 参照）
- [ ] **GitHub Environments に `prod` を作成し、必須レビュアーを設定**
  - これが prod デプロイの承認ゲートになります（[05](05-decisions.md) A-42）
  - 設定漏れがあっても事故にはならず、デプロイが IAM レベルで失敗するだけです
- [ ] **`main` ブランチの保護設定**（PR 必須 / 必須ステータスチェック / force push 禁止）
- [ ] **`ANTHROPIC_API_KEY` を prod の Secrets Manager に手動投入**

```bash
# 値を一時ファイルに書いて投入し、投入後に必ず削除する
aws secretsmanager put-secret-value \
  --secret-id s-and-p-prod-anthropic-api-key \
  --secret-string file://./key.json

rm key.json
```

> **なぜ手動か:** Terraform で管理すると **state ファイルに平文で記録されます**
> （[06 §2-3](06-cicd-and-environments.md)）。GitHub Secrets 経由で Terraform に渡す設計も
> 同じ問題を抱えます。**人間が 1 回だけ投入するのが最も安全**です。
> 一時ファイルは**リポジトリ外**に作ってください。

### コスト防御

- [ ] **AWS Budgets** で月 $20 のアラート（AWS 分 $2.80 の約 7 倍）
- [ ] **Claude Console の使用量上限** を月 $20 程度に設定
  - ⚠️ **こちらが総額の 73%** です。AWS Budgets だけでは主要部分を監視できません

---

## 3. 現在ブロックされている項目

| 項目 | 状態 | 必要な対応 |
|---|---|---|
| **GitHub への push** | ⏳ 認証エラー | キーチェーンに別アカウント（`kenken7171`）の認証情報が残っており、`void0701` の権限がない。`gh auth login` で解決 |

```bash
brew install gh
```

```bash
gh auth login
```

```bash
cd /Users/ken/work/s_and_p && git push -u origin main
```

**コミットはローカルに保全済み**（60ファイル / 9,546行）なので、作業は失われていません。

---

## 4. ✅ Phase 2 の判断（**2026-11-25 すべて確定**）

**ken さんより「全て推奨で問題ない」との回答を得ました。** 以下は経緯の記録で、
正本は [05](05-decisions.md) A-137〜A-150 です。

| 項目 | 確定 | 決定# |
|---|---|---|
| 日次の実行日 | **火〜土** | A-137 |
| Lambda ランタイム | **Python 3.12** | A-138 |
| zip のデプロイ | **直接アップロード** | A-139 |
| dev の API キー | **置かない** | A-140 |
| 過去レポートの再生成 | **毎朝作り直す** | A-141 |
| 通知先アドレス | **Terraform に置かず手動購読** | A-143 |


### 4-1. Lambda の Python ランタイム

ローカルの Python が **3.12** になりました（Homebrew から 3.14 が消えたため）。

| | 3.12（推奨） | 3.13 |
|---|---|---|
| ローカルとの差 | **なし** | あり |
| 新しさ | 1 世代前 | 最新 |

**推奨: 3.12。** 検証環境と本番環境の差分を減らすほうが、得られる新しさより価値があります。

### 4-2. Lambda zip のデプロイ方法

zip は**実測 6.3MB**（直接アップロード上限 50MB）でした。

| | 直接アップロード（推奨） | S3 経由 |
|---|---|---|
| HCL の複雑さ | 単純（`filename` + `source_code_hash`） | S3 バケットとオブジェクトの管理が増える |
| 50MB 制限 | 余裕（6.3MB） | 制限なし |

**推奨: 直接アップロード。** 当初 S3 経由を選んだ理由（pandas で 50MB を超える懸念）が
実測で消滅したため（[05](05-decisions.md) A-46 改訂）。

### 4-3. dev 環境に Anthropic API キーを置くか

**推奨: 置かない**（[05](05-decisions.md) A-126）。

- dev の目的は S3 / OAC / IAM / Scheduler の**配線検証**
- スモークテストは `dry_run: true` で行うため LLM を呼びません
- 実際の解析品質の検証はローカルで足ります（Phase 1 で実施済み）
- 「dev はシークレットを持たない」性質が保たれ、コストも $0.40/月 抑えられます

---

## 5. S0 で拒否された場合の分岐

検証スパイクでいずれかの操作が拒否された場合、以下から選びます。

| 選択肢 | 内容 | 代償 |
|---|---|---|
| **A. 高度な機能を有効化** | AWS 管理の SCP から解放される | **取り消し不可**／**支出上限を失う**／管理負荷増（[07 §A-0-P](07-prerequisites.md)） |
| **B. 別アカウントを advanced で作成** | 制約のないアカウントを新規作成。**東京リージョンを選べる** | アカウントが 2 つになる／クレジットが分散 |
| **C. 拒否された機能を使わない構成に変更** | 例: OIDC が不可なら GitHub Secrets + IAM ユーザー | セキュリティ水準が下がる |

**どれを選ぶかは拒否された内容によります。** 結果を見てから判断しましょう。

---

## 6. Claude が担当する作業

参考までに、こちら側のタスクです。ken さんの作業待ちの間に進められるものには ⏩ を付けています。

| # | 作業 | 状態 |
|---|---|---|
| 1 | 検証スパイク用の最小 HCL（`infra/spike/`） | ✅ 完了 |
| 2 | `lambda_handler.py`（Secrets Manager 取得を含む） | ✅ **完了 2026-11-25** |
| 3 | `ci.yml`（lint / 型 / テスト / gitleaks / pip-audit） | ✅ **完了 2026-11-25** |
| 4 | Lambda パッケージのビルドスクリプト | ✅ **完了 2026-11-25** |
| 5 | `infra/bootstrap/`（state + OIDC + 3 ロール） | ✅ **完了 2026-11-25** |
| 6 | `infra/modules/site`（S3 + Amplify アプリ） | ✅ **完了 2026-11-25** |
| 7 | `infra/modules/pipeline`（Lambda + IAM + Scheduler ×4） | ✅ **完了 2026-11-25** |
| 8 | `infra/modules/secrets`（Secrets Manager + CMK） | ✅ **完了 2026-11-25** |
| 9 | `infra/envs/{dev,prod}` | ✅ **完了 2026-11-25** |
| 10 | `deploy-dev.yml` / `deploy-prod.yml` | ✅ **完了 2026-11-25** |
| 11 | `checkov` を CI に追加 | ✅ **完了 2026-11-25** |

### ✅ 1〜4 の完了内容（2026-11-25）

**AWS を一切呼ばずに実装・検証しました。** 追加・変更したモジュール:

| ファイル | 役割 |
|---|---|
| `src/market_report/lambda_handler.py` | エントリポイント。ハンドラ指定は **`market_report.lambda_handler.handler`** |
| `src/market_report/publishing/s3.py` | `Publisher` の S3 実装。`get_many` で**並列読み込み**（R-5） |
| `src/market_report/publishing/amplify.py` | サイト全体を zip で置き換えるデプロイヤ（R-3） |
| `src/market_report/notify.py` | SNS 通知（A-148 の 1 層目） |
| `src/market_report/pipeline.py` | `build_site()` を分離。**保存済みレコードから全ページを描き直す**（A-141） |
| `src/market_report/rendering/html.py` | `generated_at` をレコードの値から取る（A-142） |
| `scripts/build_lambda.sh` | zip ビルド。**TOML 同梱と 50MB 超過を検査して失敗する** |
| `requirements-lambda.txt` | Lambda に載せる依存を**バージョン固定** |
| `.github/workflows/ci.yml` | lint / format / mypy / pytest / zip ビルド / gitleaks / pip-audit |
| `tests/fake_aws.py` | S3 / Amplify / Secrets Manager / SNS のスタブ |

#### ビルド実測

| | 値 | 上限 |
|---|---|---|
| 展開後 | **28.6 MB** | 250 MB |
| zip | **6.2 MB** | 50 MB |

`boto3` / `botocore` が入っていないことを確認済み（ランタイム同梱を使う / A-150）。
**zip のサイズは以前の実測 6.3MB から変わっていません** — 以前の計測も extra 抜きの
依存リストで行っていたためです。R-8 の価値はサイズ削減ではなく、
`pyproject.toml` の `anthropic[bedrock]` を素直にインストールすると
**28MB 混入する状態を構造的に防いだ**ことにあります。

#### 検証

| 検査 | 結果 |
|---|---|
| `ruff check` / `ruff format --check` | ✅ |
| `mypy src tests` | ✅ **46 files, no issues** |
| `pytest` | ✅ **82 passed**（Phase 1 時点の 51 から +31） |
| `pip-audit -r requirements-lambda.txt` | ✅ No known vulnerabilities |
| zip の中身だけで `import` | ✅ Lambda レイアウトを再現して確認 |

**ハンドラは AWS なしで通ります。** S3 / Amplify / Secrets Manager / SNS を
スタブに差し替え、3 層の S3 書き込み・サイト全体の配信・SNS 通知・
シークレット取得を 11 本のテストで押さえています。

> ### GitHub Actions の SHA 固定について
>
> A-69 のとおりコミット SHA で固定しました。**SHA は GitHub API で実在を確認
> してから書いています**（記憶で書くと存在しない SHA になり CI が起動しません）。
>
> | Action | バージョン |
> |---|---|
> | `actions/checkout` | v7.0.1 |
> | `actions/setup-python` | v7.1.0 |
> | `actions/upload-artifact` | v7.0.2 |
> | `gitleaks/gitleaks-action` | v3.0.0 |
>
> `pip-audit` は**環境ではなく `requirements-lambda.txt` を監査**します。
> 環境を対象にすると editable install の `market-report` が PyPI に無く、
> `--strict` が常に失敗します（実際に踏みました）。

---

## 8. 🔍 Phase 2 着手前レビュー（2026-11-25 実施）

Phase 2 の設計を、**実際のコードが Lambda で動くか**という観点で再検証しました。
机上のレビューではなく、Lambda のパッケージ構成を再現した実行と、Amplify の API の
実機検証を行っています。

### 結果: 🔴 着手前に直すべき 4 件 / 🟡 設計の穴 4 件 / 🔵 確認 4 件

> ### ✅ 進捗（2026-11-25）
>
> | # | 状態 |
> |---|---|
> | **R-1** 設定の読み込み経路 | ✅ **修正済み。** TOML を `src/market_report/resources/` に同梱し `importlib.resources` で解決。Lambda レイアウトでの読み込みを**回帰テスト化**（`tests/test_config.py`） |
> | **R-4** mypy 37 件 | ✅ **解消済み。** `mypy src tests` が **Success: no issues found in 37 source files**。`# type: ignore` を 19 個撤去し、うち 1 件は実際の変数衝突バグだった |
> | **R-8** 依存の整理 | ✅ **修正済み。** `pydantic-settings` を削除、`anthropic[bedrock]` を `bedrock` extra へ分離 |
> | **R-2** Lambda の読み取り権限 | ✅ **ハンドラ側は実装済み。** `S3Publisher` が読み書き両方を持つ。IAM への反映は `infra/modules/pipeline` で行う |
> | **R-3** Amplify の全置き換え | ✅ **実装済み。** `build_site()` で全ページを描き直し、zip 1 本で送る |
> | **R-5** 逐次 S3 GET | ✅ **実装済み。** `S3Publisher.get_many()` で並列化（既定 16 並列） |
> | **R-6** 失敗通知の検知 | ✅ **1 層目を実装済み。** ハンドラが捕捉して SNS に送り、再送出して `Errors` を立てる。2 層目のアラームは Terraform 側 |
> | **R-13** IAM の description に日本語が入らない | ✅ **修正済み**（2026-11-27）。実機 apply で発覚。`scripts/check_iam_descriptions.py` を CI に追加して再発を防いだ |
> | **R-14** OIDC の sub に数値 ID が必要 | ✅ **修正済み**（2026-11-27）。実機のクレームを確認して判明。bootstrap の再 apply が必要 |
> | **R-15** Lambda のメモリ上限 512MB | ✅ **修正済み**（2026-11-27）。dev の apply で発覚。実測で 512MB は約 10 年分もつ |
> | **R-16** CI に secrets CMK が必要 | ✅ **修正済み**（2026-11-27）。`kms:ViaService` で Secrets Manager 経由に限定。bootstrap の再 apply が必要 |
> | **R-17** Amplify の CreateApp スロットリング | ⏳ **時間を置いて再 apply**。コード変更は不要。**アプリ数は 1/25 でクォータ余裕あり**＝純粋な API レート制限 |
> | **R-18** `GetResourcePolicy` が不足 | ✅ **修正済み**（2026-11-27）。bootstrap の再 apply が必要 |
> | **R-19** シークレット名が 7 日ロックされた | ✅ **修正済み**（2026-11-27）。猶予を 0 に。既存の名前は `--force-delete-without-recovery` で解放する |
> | R-9 / R-12 | 未着手 |
>
> **検証:** `ruff check` ✅ / `ruff format` ✅ / `pytest` ✅ **57 passed**（+6）/ `mypy src tests` ✅
> 日次・週次の `--dry-run` を実データで通して回帰がないことを確認済み。

---

### ✅ R-1. `load_settings()` は Lambda のレイアウトで必ず失敗する → **修正済み 2026-11-25**

**[確認済: 実機で再現 2026-11-25]**

`config.py` の `_project_root()` は `Path(__file__).resolve().parents[2]` です。
Lambda のパッケージ展開先は `/var/task/market_report/` なので `parents[2]` は **`/var`**
になり、`/var/config/settings.toml` を探して落ちます。

```
project_root -> .../lamsim/var
FileNotFoundError: 設定ファイルが見つかりません: .../lamsim/var/config/settings.toml
```

さらに `pyproject.toml` の `package-data` は `rendering/templates/*.j2` のみで、
**`config/*.toml` は zip に入りません**。

**対策（推奨）:** TOML をパッケージ内（`src/market_report/config/`）に移し、
`importlib.resources` で解決する。`_project_root()` に依存しなくなり、
ローカルと Lambda で経路が一致します。

### 🔴 R-2. Lambda の IAM に**読み取り権限が無い**

[06 §3-0](06-cicd-and-environments.md) の権限表は `s3:PutObject` だけですが、
`run_daily` は最後に `rebuild_index` を呼びます（`pipeline.py:160`）。これは
**過去の全日次レコードを S3 から読みます**（`list_keys` + `get_text`）。
現在の設計のままだと、初日は通っても 2 日目以降に `AccessDenied` で落ちます。

| 追加が必要な権限 | 理由 |
|---|---|
| `s3:GetObject` / `s3:ListBucket` | `rebuild_index` / `run_period` の読み取り（上記） |
| `amplify:CreateDeployment` / `StartDeployment` / `GetJob` | 配信（§9 で決定済みだが §3-0 の表に未反映） |
| `sns:Publish` | 失敗通知（A-53）。R-6 参照 |
| `logs:CreateLogGroup` | ロググループを Terraform で先に作るなら不要 |

### 🔴 R-3. Amplify の手動デプロイは**サイト全体の置き換え**

**[確認済: 実機検証 2026-11-25]**

`--file-map` に載せたが**アップロードしなかったファイルは消えます**。
md5 は「変更なしなので据え置き」の印にはなりません。

```
file-map に 3 件を宣言 → index.html のみ PUT → start-deployment
  /                200  （更新された）
  /reports/a.html  301 → 404  ★消えた
  /reports/b.html  301 → 404  ★消えた
```

zip でサイト全体を送れば復旧しました。つまり **毎朝、全レポートを含む zip を作る**
必要があります。

**対策（推奨）:** 日次データから**全レポートを毎回レンダリングして 1 つの zip を送る**。
`rebuild_index` がすでに全日次レコードを読んでいるため、レンダリングの追加コストは
CPU だけ（Jinja2 で 1 件 1ms 程度）です。副産物として**テンプレート変更が過去分にも
反映される**という利点もあります。

| 実測 | 値 |
|---|---|
| HTML 96KB → zip | **33KB（約 3 倍圧縮）** |
| 1 年分（約 260 本）の見込み | HTML 7.7MB → **zip 約 2.6MB** |
| 5 年分 | HTML 38MB → **zip 約 13MB** |

サイズは問題になりません。

### ✅ R-4. `ci.yml` に mypy を入れると初日から赤になる → **解消済み 2026-11-25**

**[確認済: 実行 2026-11-25]**

| チェック | 結果 |
|---|---|
| `ruff check` | ✅ All checks passed |
| `ruff format --check` | ✅ 36 files already formatted |
| `pytest` | ✅ 51 passed |
| **`mypy src`** | ❌ **37 errors in 8 files** |

内訳は `unused-ignore` 13 件（`warn_unused_ignores = true` のため不要な
`# type: ignore` が検出されたもの・機械的に削除可）と、実質的な型エラー 24 件です。
`pipeline.py` 15 件 / `fixtures.py` 11 件 / `cli.py` 5 件。

**[06 §4-4](06-cicd-and-environments.md) は `ci.yml` に mypy を含める設計**なので、
**ワークフローを書く前に解消が必要**です。

#### 実施内容（2026-11-25）

`# type: ignore` を貼って黙らせるのではなく、**型が合うようにコードを直しました**。

| 対象 | 原因 | 対処 |
|---|---|---|
| `fixtures.py` 11 件 | ドライラン用の定数が素の `str` タプル | `models` の `Literal` 別名で注釈。**綴り間違いが型エラーになる**ので安全性が上がった |
| `pipeline.py` 13 件 | `for ptype in ("weekly", ...)` が `str` に推論され、`PeriodType` キーの dict に使えなかった | `PERIOD_TYPES: tuple[PeriodType, ...]` を定義。**ignore 13 個が全部消えた** |
| `pipeline.py` 1 件 | **代入式（`:=`）の変数が関数スコープに漏れ**、日次用の `r` と期間用の `r` が衝突していた | 期間側を `rec` に改名。ignore が隠していた実バグ |
| `cli.py` 4 件 | 同一関数で `record` を `DailyRecord` と `PeriodRecord` に使い回し | 期間側を `period` に改名 |
| `rss.py` 1 件 | `datetime(*tm[:6], tzinfo=UTC)` は `tm` の長さが不定だと `tzinfo` が二重指定になりうる | 6 要素に明示的に展開 |
| `market.py` 1 件 | `rows` が `list[Quote]` と `list[Quote] | None` で衝突 | 後者を `history` に改名 |
| `aggregate.py` 2 件 | `None` 除外が内包表記の条件側にあり narrowing が効かない | 代入式に変更。**ignore のコード指定自体が間違っていた**（`assignment` が漏れていた） |
| `analyzer.py` 2 件 / `doctor.py` 1 件 | 分岐で別の型を同じ変数に入れていた | 変数を分離 / `Any` で受ける |
| テスト側 7 件 | 同様の `Literal` と不要 `ignore` | 同様に修正。**CI は `mypy src tests` で両方を検査する** |

---

### 🟡 R-5. `rebuild_index` は S3 GET が日数に比例する

アーカイブ 1 行に必要なのは 4 項目（日付・スコア・要約 70 字・騰落率）だけですが、
**1 日あたり 36KB の JSON を全件・逐次で読んでいます**。5 年後は 1,300 回の逐次 GET です。

ただし R-3 により**全日次レコードは結局必要**なので、軽量マニフェストを別に持つ意味は
薄いです。**`ThreadPoolExecutor` で並列化する**のが正解で、`rss.py` に同じ実装パターンが
すでにあります。

### 🟡 R-6. 失敗通知の「検知方法」が決まっていない

A-53 で「SNS → メール」は決まっていますが、**何が失敗を検知するか**が未設計です。
2 層に分けるのを推奨します。

| 層 | 対象 |
|---|---|
| ハンドラ内の `try`/`except` → `sns:Publish` | 業務エラー（全フィード失敗、LLM エラー等）。原因を本文に書ける |
| CloudWatch アラーム（`Errors` メトリクス）→ SNS | ハンドラに到達しない異常（タイムアウト、OOM、import 失敗） |

前者だけでは**タイムアウトが無通知**になります。

### 🟡 R-7. AWS プロバイダ 5.x は Amplify のスロットリングで**無言で止まる**

**[確認済: 実機検証 2026-11-25]**

`aws_amplify_app` の作成で、プロバイダ 5.100.0 は **14 分間ハング**しました
（出力なし・CPU 1.5 秒＝待機のみ）。6.74.0 に上げると原因が即座に出ました。

```
BadRequestException: Rate exceeded while calling CreateApp, please slow down or try again later.
```

5.x が内部でリトライを続け、何も表示しないまま待っていたということです。

**対策:** `required_providers` を **`~> 6.0` に固定**する（[06 §2-5](06-cicd-and-environments.md)
は `~> 5.0` なので要改訂）。あわせて **Amplify の `CreateApp` はレート上限が厳しい**点に
注意が必要です。dev / prod の 2 個を作るだけなら問題になりませんが、
**`terraform destroy` → `apply` を繰り返す検証はスロットリングされます**。

### ✅ R-8. Lambda の依存に無駄がある → **修正済み 2026-11-25**

| 項目 | 現状 | 対策 |
|---|---|---|
| `anthropic[bedrock]` | botocore 27MB + boto3 1.4MB を引き込む | **Lambda 用は `anthropic`（extra なし）**。boto3 はランタイムに同梱済み |
| `pydantic-settings` | **コード中で未使用**（`grep` で参照 0 件） | 依存から削除 |

---

### 🔵 R-9. 07:00 JST は冬時間だと米国の引けから 1 時間しかない

07:00 JST = 17:00 EST（前日）/ 18:00 EDT（前日）。夏時間は引けから 2 時間ありますが、
**冬時間は 1 時間**です。今日の実行は 14:11 JST で完全なデータが取れましたが、
07:00 時点での可用性は未確認です。

**対策:** 本番切り替え前に、**07:00 JST にローカルで 1 回実行**して
`fetch_errors` と `reference_date` を確認する。費用は $0.24、AWS 不要です。

### 🔵 R-10. ✅ `customRules` は不要（§9 の残課題 #3 を解消）

存在しないパスは **301 を 1 回経て 404** で終わりました。Amplify の既定の
pretty-URL 挙動です。追加設定は要りません。

```
/nope.html → 301 /nope/ → 404
```

### 🔵 R-11. 着手前の未決は 3 件ではなく **5 件**

§4 の 3 件に加え、[00 §B](00-open-items.md) の 2 件も Phase 2 開始前の判断です。

| # | 項目 | 推奨 |
|---|---|---|
| B-1 | 日次を「火〜土」にするか | **する**（空振り週 2 回を排除。[09 §2](09-periodic-reports.md)） |
| B-2 | 失敗通知のメールアドレス | **ken さんの決定が必要**（技術的推奨なし） |

**B-2 だけは推奨を出せません。** SNS サブスクリプションに必要なので、
`infra/` を書く前に決めてください。

### ✅ R-16. CI に secrets 用 CMK を渡さないとシークレットの容器が作れない

**[確認済: 実機 apply 2026-11-27]**

prod の `terraform apply` で Secrets Manager の作成が失敗しました。

```
Error: creating Secrets Manager Secret (s-and-p-prod-anthropic-api-key):
api error AccessDeniedException: Access to KMS is not allowed
```

A-65 で「**CI のデプロイロールが API 認証情報を復号できないようにする**」ため、
`gha-deploy-*` には **state 用 CMK だけ**を渡していました。しかし
`CreateSecret` に顧客管理キーを指定すると、**呼び出し元の資格情報で KMS を
使う必要がある**ため、容器すら作れませんでした。

#### 設計の矛盾をどう解いたか

「鍵を一切渡さない」ではなく **「Secrets Manager 経由でだけ使わせる」** に変更しました。

```hcl
condition {
  test     = "StringEquals"
  variable = "kms:ViaService"
  values   = ["secretsmanager.ap-southeast-2.amazonaws.com"]
}
```

`kms:ViaService` を付けると、**CI は KMS を直接叩けません**。そして
`secretsmanager:GetSecretValue` / `PutSecretValue` は明示的に Deny して
あるため、**暗号文を手に入れる経路自体がありません**。

| 経路 | 可否 |
|---|---|
| KMS を直接呼んで復号 | ❌ `ViaService` で不可 |
| Secrets Manager から値を取得 | ❌ 明示的 Deny |
| シークレットの容器を作る | ✅ 必要なのでこれだけ通す |

**A-65 の意図（CI は値を読めない）は保たれています。** 防御の層が
「鍵を渡さない」から「鍵の使い道を限定する + 値の取得を拒否する」に
変わっただけです（A-175）。

> この文は **prod のみ**に付きます（`dynamic` ブロックで `each.key == "prod"`
> のときだけ生成）。dev はシークレットを作らないため不要です（A-140）。

---

### ✅ R-19. 作成が中途で失敗したシークレットが「名前のロック」でデッドロックした

**[確認済: 実機 apply 2026-11-27]**

R-18 を直して再 apply したところ、別の失敗になりました。

```
Error: creating Secrets Manager Secret (s-and-p-prod-anthropic-api-key):
InvalidRequestException: You can't create this secret because a secret with
this name is already scheduled for deletion.
```

AWS 側の記録を見ると、**作成 10 分後に削除されていました**。

```
Created: 2026-11-27T15:11:52   ← R-18 で失敗した apply が作成
Deleted: 2026-11-27T15:21:33   ← 次の apply が「置き換え」を試みて削除
HasValue: false
```

#### 連鎖の仕組み

1. 作成は成功したが、**直後の `GetResourcePolicy` で権限不足**になり apply が失敗（R-18）
2. Terraform は中途半端に作られたリソースを**置き換え対象**として記録する
3. 次の apply が destroy → `DeleteSecret`（**7 日の削除待機**）
4. その名前は 7 日間再利用できず、create が上記エラーで失敗

**権限を直しても、名前がロックされたままなので先に進めません。**

#### 対処 1: 猶予を 0 にした（A-178）

```hcl
recovery_window_in_days = var.recovery_window_in_days   # 既定 0
```

**7 日の猶予がこの鍵を守るものは少ない。** 中身は再発行できる API キーで、
失っても Console で作り直して入れ直すだけです。一方で名前のロックは
**復旧を 7 日間ブロックする**実害があります。

誤削除への本当の防御は猶予期間ではなく、**CI が値を読めないこと**
（A-175）と運用規律です。

> AWS の仕様で 1〜6 日は指定できません（0 か 7〜30）。`validation` で弾いています。

#### 対処 2: 今ロックされている名前を解放する

既存のものは `0` に変えても自動では解放されません。**値が入っていないので
失うものはありません。**

```bash
aws secretsmanager delete-secret \
  --secret-id s-and-p-prod-anthropic-api-key \
  --force-delete-without-recovery
```

これで名前が即座に空き、次の apply が作り直します。

---

### ✅ R-18. Terraform はリソースポリシーを**読み戻す**ので `GetResourcePolicy` も必要

**[確認済: 実機 apply 2026-11-27]**

R-16 を直したあと、シークレットの**容器は作成されましたが**、直後に失敗しました。

```
Error: reading Secrets Manager Secret (...) policy: AccessDeniedException:
User: .../s-and-p-gha-deploy-prod/GitHubActions is not authorized to perform:
secretsmanager:GetResourcePolicy
```

`PutResourcePolicy` は許可していましたが、**Terraform は apply 後に現在の
ポリシーを読み戻して state に反映する**ため `GetResourcePolicy` も必要でした。

#### `Describe*` / `List*` のワイルドカードでは足りない

`GetResourcePolicy` は `Get*` に含まれますが、`Get*` を許可すると
`GetSecretValue` も入ってしまいます。ただし **IAM では明示的 Deny が常に
Allow に勝つ**ため、`Get*` + `GetSecretValue` の Deny という書き方でも
安全です。それでも**意図が読めるよう個別に列挙**しました（A-177）。

```
CreateSecret / DeleteSecret / UpdateSecret
DescribeSecret / ListSecrets / ListSecretVersionIds
GetResourcePolicy / PutResourcePolicy / DeleteResourcePolicy
TagResource / UntagResource
```

`GetSecretValue` / `PutSecretValue` の明示的 Deny はそのままです。

> **容器は作成され、値は未投入の状態で残っています。** 再 apply は
> 作成済みのリソースを飛ばして続きから進みます。
>
> ```
> Name: s-and-p-prod-anthropic-api-key
> KmsKeyId: arn:aws:kms:...:key/6b2a2dd7-...
> HasValue: false   ← 設計どおり（A-33）
> ```

---

### 🔍 権限の抜けを繰り返さないための棚卸し（2026-11-27）

R-16 / R-18 と**権限不足を 2 回続けて踏んだ**ため、デプロイロールの許可と
prod が作るリソースを機械的に突き合わせました。

| サービス | 許可の形 | リスク |
|---|---|---|
| amplify / budgets / cloudwatch / lambda / logs / s3 / scheduler / sns | **`:*`（ワイルドカード）** | 抜けは起きない |
| **iam** | 列挙 12 個 | `aws_iam_role` / `aws_iam_role_policy` に必要な操作は揃っている |
| **secretsmanager** | 列挙 11 個 | R-18 で補完済み |
| **kms** | 列挙 + 条件付き 2 文 | state 用と secrets 用（ViaService 限定）で分離 |

**失敗した 2 回はどちらも「列挙のみ」のサービスでした。** 残る列挙サービスの
操作は洗い出し済みなので、同じ原因での失敗は起きにくい見込みです。

> ワイルドカードのままにしているのは、**Terraform でインフラを作るロールは
> 作成対象サービスに対して広い権限が必要**で、絞っても抜けを追いかけ続ける
> ことになるためです。代わりに
> **state の書き込み範囲**（環境別プレフィックス / A-166）、
> **secrets 用 CMK の用途限定**（ViaService / A-175）、
> **値の取得の明示的 Deny**（A-167）
> で実害のある操作を止めています。

---

### ⏳ R-17. Amplify の `CreateApp` が同日中は再度スロットリングされた

prod の apply では Amplify アプリの作成も失敗しました。

```
BadRequestException: Rate exceeded while calling CreateApp, please slow down or try again later.
```

R-7 で既知の制約ですが、**dev の作成（02:03）から約 4 時間後でも解けていません**。
検証用の `filemap-probe` や Terraform の失敗試行も回数に数えられている可能性があります。

**対処は時間を置いて再 apply するだけ**です。Terraform は作成済みの
リソースを state で把握しているため、残りだけが作られます。

#### アプリ数のクォータではない

`service-quotas` で確認したところ Amplify の制限は **Apps: 25** だけで、
現在は **1 個**（dev）しかありません。つまり**純粋な API のレート制限**で、
`service-quotas` には現れません（Lambda のメモリ上限 R-15 と同じパターン）。

```
2026-11-27 02:03  dev の CreateApp     → ✅ 成功
2026-11-27 05:57  prod の CreateApp    → ❌ Rate exceeded
2026-11-27 06:11  prod の CreateApp    → ❌ Rate exceeded
```

**1 日あたり数回程度の非常に遅い補充**と見られます。CLI で作って
`terraform import` する案も考えましたが、**レート制限はアカウント単位なので
CLI でも同じく弾かれます**。待つのが唯一の手です。

> **恒久的な対策が必要なら**、Amplify アプリだけ AWS CLI で 1 回作って
> `terraform import` する方法があります。アプリの作成は環境ごとに 1 回なので、
> 運用上は再 apply で足ります。

---

### ✅ R-15. このアカウントは Lambda のメモリ上限が 512MB

**[確認済: 実機 apply 2026-11-27]**

dev の `terraform apply` で Lambda の作成が失敗しました。

```
Error: creating Lambda Function (s-and-p-dev-report): api error ValidationException:
'MemorySize' value failed to satisfy constraint:
Member must have value less than or equal to 512
```

A-128 で 1024MB と決めていましたが、このアカウント（プロジェクト方式）では
**512MB が上限**です。**`service-quotas` には現れません**（API の
ValidationException でしか分かりません）。

#### 他の上限は問題なし

| クォータ | 値 | 本構成 | |
|---|---|---|---|
| Function timeout | 900 秒 | 600 秒 | ✅ |
| Concurrent executions | 400 | 予約 2 | ✅ |
| Function and layer storage | 300 GB | 6.2 MB | ✅ |

#### 512MB で足りるか — 実測した

`build_site()` が過去の全レコードを読むため、メモリは日数に比例します。
日次レコードを複製して規模を変えて測りました。

| 日数 | ページ | zip | 最大 RSS |
|---|---|---|---|
| 3 | 5 | 0.0 MB | **34.7 MB** |
| 60 | 62 | 0.6 MB | 44.0 MB |
| 150 | 152 | 1.5 MB | 61.5 MB |
| 300（約1.2年） | 302 | 3.0 MB | **93.8 MB** |
| 600（約2.3年） | 602 | 6.0 MB | **150.0 MB** |
| 1300（約5年） | 1302 | 13.1 MB | **282.0 MB** |

**約 0.19 MB/日** で増えるので、512MB に達するのは **約 10 年後**です
（260営業日/年）。当面は問題になりません。

> **CPU も下がります。** Lambda の CPU 配分はメモリに比例するため、
> 1024MB の約半分になります。ただし実行時間の大半は LLM の応答待ち
> （130〜155 秒のネットワーク I/O）なので、体感への影響はほぼありません。

#### 対処

- `memory_size` を変数にし、既定 512 + **512 超を拒否する `validation`** を付けた（A-173）
- CloudWatch の `Max Memory Used` が 450MB に近づいたら、クォータの
  引き上げ申請かアーカイブの打ち切りを検討する（運用後の監視項目）

---

### ✅ R-14. GitHub の OIDC `sub` には不変の数値 ID が入る

**[確認済: 実機 2026-11-27]**

bootstrap の apply 後も CI が OIDC でロールを引き受けられませんでした。

```
Could not assume role with OIDC: Not authorized to perform sts:AssumeRoleWithWebIdentity
```

信頼ポリシー・ロール ARN・`aud` はすべて一致していました。**GitHub が実際に
送っているクレームを一時ワークフローで確認**したところ、原因が判明しました。

```
実際:  repo:void0701@360565473/global-market-ai-reporter@1469782982:ref:refs/heads/main
想定:  repo:void0701/global-market-ai-reporter:ref:refs/heads/main
```

**`owner@<owner_id>` と `repo@<repo_id>` の形で数値 ID が入ります。**
多くの解説記事は名前だけの形を載せていますが、それでは一致しません。

#### なぜこの仕様なのか

**リポジトリ名の使い回し攻撃**を防ぐためです。名前だけで信頼すると、
リポジトリやオーナーを削除した後に第三者が同じ名前を取得し、信頼関係を
引き継げてしまいます。数値 ID は再利用されないため、ID を含めるほうが安全です。

#### 切り分けが難しい理由

`Not authorized to perform sts:AssumeRoleWithWebIdentity` は

- (a) 信頼ポリシーの `sub` / `aud` が合っていない
- (b) SCP で STS の操作自体が拒否されている

の**どちらでも同じ文面**になり、クライアント側からは区別できません。
S0 スパイクは OIDC プロバイダとロールの**作成**しか検証しておらず、
**実際に引き受けられるか**は未検証でした。ここが穴でした。

#### 対処

ID を変数化し、`locals.oidc_repo` で前半を組み立てて 3 ロールで共有します
（A-171）。ワイルドカードは使いません。

```bash
gh api repos/<owner>/<repo> --jq '"owner_id: \(.owner.id)  repo_id: \(.id)"'
```

> **リポジトリを作り直すと ID が変わり、デプロイが失敗します**（fail close）。
> その場合は `github_owner_id` / `github_repo_id` を更新して bootstrap を
> 再 apply してください。

**`environment:prod` の形は prod デプロイ時に確認します。** 前半の
`repo:owner@id/name@id` は同じなので通る見込みですが、未検証です。

---

### ✅ R-13. IAM の `description` に日本語を入れると apply が 400 で落ちる

**[確認済: 実機 apply 2026-11-27]**

`infra/bootstrap` の `terraform apply` で IAM ロール 3 件が失敗しました。

```
Error: creating IAM Role (s-and-p-gha-plan): api error ValidationError:
1 validation error detected: Value at 'description' failed to satisfy
constraint: Member must satisfy regular expression pattern:
[\u0009\u000A\u000D\u0020-\u007E\u00A1-\u00FF]*
```

つまり **TAB / LF / CR + ASCII 印字可能 + Latin-1 補助のみ**で、日本語は
入りません。**サーバー側の検証なので `terraform plan` では検出できません。**

#### 制約があるのは IAM だけ

botocore のサービスモデルで確認しました。

| サービス | `description` の pattern | 日本語 |
|---|---|---|
| **IAM** `roleDescriptionType` | `[\u0009\u000A\u000D\u0020-\u007E\u00A1-\u00FF]*` | ❌ |
| KMS `DescriptionType` | 制約なし（max 8192） | ✅ |
| Secrets Manager `DescriptionType` | 制約なし（max 2048） | ✅ |
| CloudWatch `AlarmDescription` | 制約なし（max 1024） | ✅ |
| Scheduler `Description` | 制約なし（max 512） | ✅ |
| Lambda `Description` | 制約なし（max 256） | ✅ |
| Amplify `Description` | `(?s).*` | ✅ |

**同じ apply で KMS キーは成功しています。** IAM だけが例外でした。

#### 対処

- IAM の `description` を英語にし、日本語の説明は HCL のコメントに残した（A-170）
- **`scripts/check_iam_descriptions.py` を作り CI の `iac` ジョブに入れた。**
  plan では防げない種類のエラーなので、静的検査で止める

---

### 🔵 R-12. ドキュメントに CloudFront の記述が約 25 箇所残っている

§9 で Amplify に決めましたが、反映は `06` と `05` と `13` だけです。
`00` / `01` / `03` / `04` / `07` / `README` に旧記述が残っており、
**実装時に誤った設計を参照する危険があります**。R-2 の権限表もこの一部です。

---

### レビューを踏まえた作業順の修正

```
R-1 設定の読み込み経路を修正      ← 🔴 これなしに Lambda は動かない
R-4 mypy を解消                   ← 🔴 これなしに ci.yml が書けない
R-8 依存の整理                    ← 🔴 ビルドスクリプトの前提
   ↓
lambda_handler.py（R-2 R-3 R-6 を織り込む）
ci.yml / ビルドスクリプト
   ↓
R-12 ドキュメントの配信層を一括更新
   ↓
infra/bootstrap → modules → envs（プロバイダ `~> 6.0`）
```

**R-1 / R-4 / R-8 はすべて AWS 不要**です。先に片付けると、
Lambda ハンドラを書いた時点で「ローカルで Lambda と同じ経路を通る」状態になります。

### ✅ 5〜11 の完了内容（2026-11-25）

```
infra/
├── bootstrap/        state バケット + KMS ×2 + OIDC + IAM ロール ×3   … 17 リソース
├── modules/
│   ├── site/         S3（非公開）+ Amplify アプリ / ブランチ
│   ├── pipeline/     Lambda + IAM + Scheduler ×4 + SNS + アラーム + Budgets
│   └── secrets/      Secrets Manager の容器のみ（値は手動投入）
└── envs/
    ├── dev/          Scheduler 無効 / シークレットなし / ログ 7 日   … 21 リソース
    └── prod/         Scheduler 有効 / シークレットあり / ログ 30 日  … 24 リソース
```

#### `terraform plan` を実際に通しました

`validate` では provider 側の属性検証が走らないため、**実際の AWS に対して
plan を実行**しました（読み取りのみ・何も作っていません）。

| 対象 | 結果 |
|---|---|
| `infra/bootstrap` | ✅ **Plan: 17 to add** |
| `infra/envs/dev` | ✅ **Plan: 21 to add** |
| `infra/envs/prod` | ✅ **Plan: 24 to add** |
| `terraform fmt -check -recursive infra` | ✅ |
| `checkov` | ✅ **135 passed / 0 failed** |

#### checkov の 21 件の指摘に対して

**機械的に黙らせていません。** 直す価値があるものは直しました（A-159）。

| 指摘 | 対応 |
|---|---|
| `CKV_AWS_50` X-Ray 無効 | ✅ **有効にした。** 150 秒の処理の内訳が見える。月 30 回では無料枠内（A-162） |
| `CKV_AWS_116` Lambda に DLQ がない | ✅ **SNS を DLQ にした。** Scheduler は非同期呼び出しなので、リトライを使い切るとハンドラの `try`/`except` に到達しない。既存の通知トピックを再利用（A-161） |
| 残り 13 種 | 理由を 1 件ずつ `.checkov.yaml` に記載して除外。VPC（NAT 費用のみ増える）、環境変数の CMK（**そもそも秘密情報を置かない**）、シークレットの自動ローテーション（**Claude API キーは非対応**）など、既に設計で評価済みのもの |

#### 実装中に見つけて直したこと

| | |
|---|---|
| **`build_site()` が 1 回の実行で 2 回走っていた** | `run_daily` 内の `rebuild_index` と配信の両方が全レコードを読んでいた。`publish_index=False` で 1 回に集約（A-160）。テストで呼び出し回数を固定 |
| **CI の `plan` がロックを取れない** | `gha-plan` は state を読むだけで書けないため、S3 ネイティブロックの取得に失敗する。`-lock=false` を指定（A-163） |
| **prod の変数が CI から読めない** | `terraform.tfvars` は `.gitignore` 済み。Repository variables から `TF_VAR_*` で渡す（A-164） |
| **Amplify の `custom_rule`** | 一度 SPA フォールバックを書いたが、**404 が 200 になり R-10 で検証した挙動を壊す**ため削除した |

---

## 7. 完了条件

- [ ] 毎朝 07:00 JST（火〜土）に日次レポートが自動生成される
- [ ] 月曜 07:00 JST に週次レポートが生成される
- [ ] 毎月 1 日・毎年 1 月 1 日に月次 / 年次が生成される
- [ ] **Amplify** の URL でレポートが閲覧できる
- [ ] 失敗時にメール通知が届く
- [ ] `main` への push で dev に自動デプロイされる
- [ ] prod デプロイに承認ゲートが効いている
- [ ] CI（lint / テスト / checkov / gitleaks）が通っている
- [ ] コスト上限が AWS と Claude Console の両方に設定されている
