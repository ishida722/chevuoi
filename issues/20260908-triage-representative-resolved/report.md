# vuoi triage: 代表カード自身が「解決済み」と判定された場合の扱いが未定義

## 背景・問い

Inbox トリアージは、重複クラスタの重複側カードを代表カードへ集約（`merge`）し、
解決済みのカードを畳む（`archive`）。畳まれる側は鮮度判定を省いているが、**代表側は
省いていない**。そのため同じランで「代表を `archive`」と「重複を代表へ `merge`」が
同時に成立しうる。仕様（`docs/spec/inbox-triage.md` の行動の決定表）はこのケースを
定めていない。

問う内容は次の 3 点。

1. この同時成立は実際に起きるか。起きるとして、何が失われるか。
2. 現状（方針 (3)）は一貫した挙動として記述できるか。
3. 取りうる 3 方針 —— (1) 代表は当該ランでアーカイブしない / (2) クラスタ全体をまとめて
   アーカイブする / (3) 現状のまま —— のトレードオフは何か。

実装は行わず、方針の決定は人間に委ねる。

## 調査方法（見たもの・手順・前提）

### 前提: 対象コードは main に存在しない

カードが挙げた `src/chevuoi/application/usecases/triage_usecase.py` と
`docs/spec/inbox-triage.md` は、**main にも本ワークツリー（`chevuoi/trello-emD4FY1Q`）にも
存在しない**。実装は親カードのブランチ `chevuoi/trello-qNH3k8sL`（コミット `18d4000`
「vuoi トリアージ機能の実装- #34」）にあり、未マージ。以降の行番号・引用はすべて
このブランチのもの。

### 手順

1. `git log --all -- <paths>` で実装コミットを特定。
2. コードリーディング（実装ブランチ）:
   - `src/chevuoi/application/usecases/triage_usecase.py`（段の順序と除外条件）
   - `src/chevuoi/domain/services/triage_policy.py`（決定表 `decide()`）
   - `src/chevuoi/domain/services/triage_clustering.py`（`build_clusters` / `choose_representative`）
   - `src/chevuoi/domain/entities/triage_card.py`（`digest()`）
   - `src/chevuoi/domain/entities/triage_judgment.py`（`abstained`）
   - `docs/spec/inbox-triage.md`（決定表・冪等性・安全側の既定）
3. `tests/unit/test_triage_usecase.py` の 34 テストを走査し、本ケースの被覆が無いことを確認。
4. 再現: 実装ブランチのツリーを `git archive` で `/tmp/triage-repro` に展開し、既存の
   テスト部品（`FakeTriageRepo` / `ScriptedJudge` / `FakeInspector` / `run`）を借りた
   再現テスト `repro_test.py` を `uv run pytest` で実行。**リポジトリのソースは変更していない。**
   成果物は `issues/20260908-triage-representative-resolved/` 配下のみ。

### 再現の条件

`MIRAI ログイン画面が落ちる` / evidence `src/a.py:1` / `base_commit` なし /
`path_missing_in_base` 真（→ `_should_judge_resolved` が真）/ 鮮度判定は
`verdict="resolved", confidence="high"`。本文は基本ケース（A〜D）で同一、ケース E だけ
代表と重複で変えている。6 ケースすべて green（＝下記の挙動を確認）。

## 結果（事実）

### F1. 代表は鮮度判定の対象から外れていない

`triage_usecase.py` の鮮度判定ループ:

```python
representatives = build_clusters(cards, confirmed)          # 134 行目付近
...
for key, card in targets.items():
    if key in representatives and str(representatives[key].id) != key:
        continue  # 重複として畳む予定のカードに鮮度判定は要らない
```

`continue` の条件は「代表が**自分でない**」。代表自身
（`representatives[key].id == key`）はこの網をすり抜けて `judge_resolved` に進む。

### F2. 決定表は 1 枚単位では記述どおりに動いている

`triage_policy.decide()` の行 2（重複）は `representative != card.id` を要求するため
代表自身は通り抜け、行 3（解決済み）が一致して `archive` を返す。決定表の行順の
バグではない。仕様が定めていないのは**クラスタ単位の不変条件**（`merge` の集約先は
そのランを生き残る）である。

### F3. 代表が自動起票カードなら、必ず重複より先に適用される

適用ループは `cards`（`fetch_open()` の作成順昇順）を回る。`choose_representative` は
`min(key=(is_auto_issued, created_at, str(id)))`、すなわち人間起票カードがあればそれを、
無ければクラスタ内の最古のカードを代表にする（仕様 86 行目と同じ規則）。本件で問題に
なるのは代表が自動起票のとき（F4）で、そのとき代表はクラスタ内の最古であり
**重複の `merge` が走る時点で代表は既にアーカイブ済み**になる。順序は決定的で、揺らがない。

人間起票カードが代表のクラスタでは、代表が重複より後に処理されることもある（作成時刻に
よらず代表になるため）。ただし人間起票カードはアーカイブされないので、本件の衝突は起きない。

### F4. 衝突が起きるのは代表も自動起票カードのときだけ

人間起票カード（`key` 空 = `is_auto_issued` 偽）は `_is_target` で落ちて `targets` に
入らないため、鮮度判定に載らない。「人間起票カードは代表にはなるが自身は畳まない」
という仕様の非対称性は保たれている。

### F5. ケース A（`--apply` + 完全一致クラスタ・本文も同一）: クラスタ全体が消え、集約コメントは投稿されない

```
plans:    [('rep', 'archive'), ('dup', 'merge')]
archived: ['trello:rep', 'trello:dup']
comment on trello:rep: 🤖 triage: ベースで解消済みと判定しアーカイブしました digest=1fffa622ba06
comment on trello:dup: 🤖 triage: 重複のためアーカイブしました digest=1fffa622ba06
```

代表への「重複カードを集約しました」コメントは**存在しない**。
`_apply` の merge が `has_comment(representative.id, digest)` で冪等判定しており、
代表自身の archive コメントが同じ `digest` を含むため抑止される（原因は F8）。
`dup` 側のコメントは「集約先: https://trello.com/c/rep」を指すが、その `rep` は
アーカイブ済み。

**ただしこの抑止が起きるのは、代表と重複の本文まで一致するときだけである。**
クラスタは正規化タイトルの一致だけで作られる（`normalize_for_similarity` はタイトルしか
見ない）のに対し、`digest()` はタイトル + 本文から作る。本文が違えば digest は衝突せず、
集約コメントはアーカイブ済みの代表に投稿される。

ケース E（同じ条件で本文だけ変えたもの）:

```
digests:  rep=c3a2e02257e7  dup=227b927261aa
plans:    [('rep', 'archive'), ('dup', 'merge')]
archived: ['trello:rep', 'trello:dup']
comments on rep: 🤖 triage: ベースで解消済みと判定しアーカイブしました digest=c3a2e02257e7
                 🤖 triage: 重複カードを集約しました digest=227b927261aa
                 - MIRAI ログイン画面が落ちる: https://trello.com/c/dup
comments on dup: 🤖 triage: 重複のためアーカイブしました digest=227b927261aa
                 集約先: https://trello.com/c/rep
```

**したがってカードの記述「集約コメントがアーカイブ済みカードに残り」は、本文が異なる
場合には正しい。本文まで同一の場合にだけ「コメントごと投稿されない」に変わる。**
なお、畳んだ先を示す「集約先: <代表の URL>」のコメントは、どちらの場合も重複カード側に残る。

### F6. ケース B（dry-run）: 矛盾したラベルが同時に立つ

```
plans: [('rep', 'label', ('triage/stale',)), ('dup', 'label', ('triage/duplicate',))]
```

破壊的操作は起きないが、`triage/stale` が付いたカードを集約先として指す
`triage/duplicate` が同時に立つ。閾値較正のための dry-run 運用では、この組み合わせを
人間が読み解くことになる。

### F7. ケース C（LLM 判定で確定したクラスタ）: 代表は `keep` になる

```
pairs: [('dup', 'rep', 0.79, exact=False)]
plans: [('rep', 'keep'), ('dup', 'merge')]
resolved judge calls: [('trello:rep', PosixPath('/tmp/triage-base'))]   ← 照会はしている
archived: ['trello:dup']
```

`_pick_judgment` は重複判定（`verdict == "duplicate"` かつ非棄権）を鮮度判定より
優先して 1 つだけ返す。そのため代表に出た `resolved` 判定が握り潰されて決定表の
行 3 に到達せず、行 6 の `keep` に落ちる。`judge_resolved` の呼び出し（LLM 予算 1 回分）は
実行された上で捨てられている。

**同じ入力・同じ判定でも、クラスタが完全一致で作られたか LLM 判定で作られたかで、
代表の運命が `archive` / `keep` に分かれる。**

### F8. （範囲外）`digest` がカード ID を含まず、完全一致の重複同士で衝突する

`TriageCard.digest()` は正規化タイトルと本文だけの sha1 先頭 12 桁で、カード ID を
含まない。完全一致の重複は定義上ダイジェストが等しくなる。

ケース D（3 枚の完全一致クラスタ。代表は archive されない通常ケース）:

```
digests: 1fffa622ba06 1fffa622ba06 1fffa622ba06
plans:   [('rep', 'keep'), ('d1', 'merge'), ('d2', 'merge')]
archived: ['trello:d1', 'trello:d2']
comments on rep: ['🤖 triage: 重複カードを集約しました digest=1fffa622ba06\n- ...: https://trello.com/c/d1\n理由: 正規化タイトルが完全一致']
```

d1 と d2 は両方 `merge`・両方アーカイブされるのに、代表に残る集約コメントは d1 の
1 件だけ。**d2 は代表に何の痕跡も残さず無言でアーカイブされる。** これは代表が
解決済みかどうかとは無関係に起きる、独立した冪等性の不具合。

### F9. 既存テストに本ケースの被覆はない

`tests/unit/test_triage_usecase.py` の 34 テストのうち、代表自身が `resolved` と
判定される状況を作るものは無い。`test_high_confidence_resolved_card_is_archived` は
単独カード、`test_distinct_verdict_does_not_hide_the_freshness_verdict` は
`distinct` 判定のケース。

### F10. 実装者自身が未決事項として申告済み

実装コミット `18d4000` のメッセージに「代表カード自身が『解決済み』と判定された場合の
扱い（仕様に記述が無く、推測で実装しない判断）」が明記されている。本カードはその申告の
チケット化であり、見落としではない。

### F11. `keep` で終わった代表は、内容が変わらない限り次のラン以降も判定対象にならない

`_is_target` は、台帳にエントリがあり digest が変わっておらず `reason != "budget"` の
カードについて、`entry.state == "planned" and entry.action in EFFECTFUL_ACTIONS` のときだけ
再判定する。`keep` は `EFFECTFUL_ACTIONS`（`label` / `merge` / `archive`）に含まれないため、
`keep` で終わったカードは**内容が変わらない限り二度と対象にならない**。重複を集約しても
代表の本文は変わらない（コメントが増えるだけ）ので、digest も動かない。

ケース F（ケース C の状態から、重複がアーカイブされた次のランを回す）:

```
run1 plans:  [('rep', 'keep'), ('dup', 'merge')]
ledger:      {'trello:rep': ('keep', 'planned', ''), 'trello:dup': ('merge', 'applied', '')}
run2 plans:  [('rep', 'skip')]          ← 単独になっても対象に戻らない
run2 resolved judge calls: []           ← 鮮度判定は二度と行われない
```

これは方針 (1) に固有の問題ではなく、F7 の LLM 判定クラスタでは**現状すでに起きている**。
「クラスタのために鮮度判定を見送る」を採る場合、見送りは次のランへ持ち越されない。

## 考察と結論

### 結論 1: カードの主張は成立する。ただし失われるものはカードの記述とずれる

F1〜F3 により、同じランでの `archive` と `merge` の同時成立は実在し、代表が自動起票なら
順序も決定的。何が失われるかは、代表と重複の本文が一致するかで分かれる（F5）。

- 本文が異なる: カードの記述どおり、集約コメントがアーカイブ済みの代表に残る（ケース E）。
- 本文まで同一: digest が衝突して集約コメントが投稿されない（ケース A）。

どちらの場合も、畳んだ先を示す「集約先: <代表の URL>」のコメントは重複カード側に残り、
アーカイブされた 2 枚はいずれも理由コメントを持つ。したがって仕様の「必ず理由コメントを
残す」という安全側の既定そのものは破られていない。**破れているのは、`merge` の集約先が
ランの終了時点でも Inbox に残っているという、仕様が明文化していない前提のほうである。**
代表側に集まるはずの「どのカードを畳んだか」の一覧が（本文同一のときに）欠けること、
および集約先そのものが Inbox から消えることが、実際の損失にあたる。

### 結論 2: 現状は「方針 (3) 現状のまま」と呼べる一貫した状態ですらない

F7 が示すのは、代表が archive されるかどうかが**クラスタの作られ方に依存する**という
こと。これは設計された規則ではなく `_pick_judgment` の副作用である。方針 (3) を
選ぶなら「代表が完全一致クラスタのときだけ archive され、そのうち本文まで一致する場合は
集約コメントが digest 衝突で投稿されない」と仕様に書き下すことになるが、そう書けること
自体が難しい。
**(3) は「決めない」ことであって「現状を追認する」ことにはならない。**

### 結論 3: 方針の比較

前提として、`judge_resolved` は**代表カード 1 枚だけ**を見る（重複側の本文や evidence は
渡さない）。代表の鮮度判定は、クラスタが持つ情報の一部しか使っていない。

| 方針 | 変更量 | 利点 | 欠点 |
|---|---|---|---|
| (1) 代表は当該ランでアーカイブしない | 鮮度判定ループの `continue` 条件から「代表でない」を外す。決定表は不変。ただし台帳の手当てが要る（下記） | 集約先がランを生き残る不変条件が保たれる。F7 で捨てられている LLM 予算 1 回も節約 | 解決済みクラスタを畳むのに 2 ラン以上必要。しかも**そのままでは 2 ラン目が来ない**（F11）。台帳が保留を持ち越すようにしないと取りこぼす |
| (2) クラスタ全体をまとめてアーカイブ | 決定表にクラスタ単位の行を追加。`decide()` の signature も変わる | 1 ランで畳み切れる。重複が同一なら代表が解決済み＝全員解決済み、という推論自体は筋が通る | 確信度 high の同一性判定だけを根拠に、evidence の異なるカードまで個別確認なしで消える。1 枚単位という決定表の構造が崩れる |
| (3) 現状のまま | なし | —— | 結論 2 のとおり記述不能。F8 の digest 衝突は独立に直す必要が残る |

**所見: (1) を推す。** 理由は 3 つ。

- 仕様が第 2 層の予算配分で既に使っている論法「解決済み判定は次のランへ持ち越しても
  損失がない」（仕様 61 行目）に乗れる。新しい原則を導入せずに済む。
  ただし**乗るには台帳側の手当てが要る**。現状の `_is_target` は前回 `keep` で終わった
  カードを内容が変わらない限り再判定しないため、クラスタのために見送った鮮度判定は
  次のランで拾い直されない（F11）。予算超過を `reason="budget"` で持ち越しているのと
  同じ仕組みで「クラスタのため保留」を台帳に残せば足りる。この手当てを含めても変更は小さい。
- 増える不変条件は 1 つ（「`merge` の集約先はランを生き残る」）だけで、決定表の構造を
  変えない。
- 安全側の既定（可逆・理由コメント必須・LLM に推測させない）と整合する。(2) は
  「同一だから解決済みも同一」という推論を LLM の同一性判定に上乗せする分、
  安全側から一歩踏み出す。

ただしこれは判断であって決定ではない。決定は人間に委ねる。

### 結論 4: F8 はどの方針を採っても直す必要がある

digest 衝突は代表の解決済み判定とは独立に発生し（F8 のケース D）、複数枚の重複を
畳むという本機能の中心的な動作を静かに壊している。冪等キーに畳まれる側のカード ID を
含める（例: `digest=<digest>/<card_id>`）のが素直な修正。別チケットとして申告済み。

## 参考（ファイル・URL・ログの場所）

### 調査対象コード（ブランチ `chevuoi/trello-qNH3k8sL` / コミット `18d4000`、main 未マージ）

| ファイル | 見た箇所 |
|---|---|
| `src/chevuoi/application/usecases/triage_usecase.py` | 134 行目付近 `build_clusters` と直後の鮮度判定ループの `continue`、`_apply` の merge 分岐、`_pick_judgment` |
| `src/chevuoi/domain/services/triage_policy.py` | `decide()` の行 2（重複）・行 3（解決済み） |
| `src/chevuoi/domain/services/triage_clustering.py` | `build_clusters` / `choose_representative` |
| `src/chevuoi/domain/entities/triage_card.py` | `digest()`（59 行目付近）、`is_auto_issued` |
| `src/chevuoi/domain/entities/triage_judgment.py` | `abstained` |
| `src/chevuoi/infrastructure/trello/trello_triage_repository.py` | `has_comment` / `archive` |
| `docs/spec/inbox-triage.md` | 「行動の決定表」（69 行目以降。表は 73〜80 行目）、第 2 層の予算配分（61 行目）、代表の選び方（86 行目）、「冪等性」「安全側の既定」 |
| `tests/unit/test_triage_usecase.py` | 34 テストの走査（本ケースの被覆なし） |

### 本フォルダの成果物

- `README.md` —— 作業の What / Why / Tasks
- `report.md` —— 本報告
- `repro_test.py` —— 再現テスト 6 ケース。実行手順はファイル先頭のドキュメント文字列
  （実装ブランチのツリーを `/tmp/triage-repro` に展開して `uv run pytest ... -s`）。
  再現ログは本報告の F5〜F8・F11 に転記済み（`/tmp/triage-repro` は調査後に削除）

### 関連

- 本カード: https://trello.com/c/emD4FY1Q
- 親カード: https://trello.com/c/qNH3k8sL （トリアージ機能の実装 #34）
- 実装コミット `18d4000`（未決事項として申告済み。F10）

## 次のアクション案

1. **【人間の決定待ち】方針の選択。** (1) 代表は当該ランでアーカイブしない /
   (2) クラスタ全体をまとめてアーカイブ / (3) 現状のまま。調査としては (1) を推す
   （結論 3）。この決定なしに実装へは進まない。
2. **仕様への明文化**（方針決定後）。`docs/spec/inbox-triage.md` の「行動の決定表」に
   クラスタ単位の不変条件を追記する。
   - (1) を採るなら:「重複クラスタに属するカードは、代表を含め当該ランでは鮮度判定を
     行わない。クラスタが解消された次のランで判定する」
   - (2) を採るなら:「代表が解決済みと確定した場合、クラスタ全体を `archive` とする
     （`merge` は行わない）」
   - いずれの場合も「`merge` の集約先はランの終了時点で Inbox に残っている」という
     不変条件を明文化しておくと、同種の見落としを防げる。
3. **実装とテスト**（方針決定後）。方針 (1) なら鮮度判定ループの `continue` 条件の変更に
   加えて、台帳に「クラスタのため保留」を残して次のランで拾い直す手当てが要る（F11）。
   `repro_test.py` のケース A・B・E・F をそのまま回帰テストに転用できる。
   ケース C（`_pick_judgment` による resolved 判定の握り潰し）も、方針 (1) なら
   「クラスタ内では鮮度判定を行わない」ことで自然に解消する。
4. **F8 の digest 衝突を別チケットで修正。** 方針の決定を待たずに着手できる。
   申告済み（kind=bug）。
5. 本カードは方針が決まり仕様に反映された時点で Close。実装は別カードに分ける。
