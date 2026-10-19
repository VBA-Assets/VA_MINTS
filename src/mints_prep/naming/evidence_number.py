"""証拠番号パターンの判定（要件定義書 4.3）。

既存のファイル名が、証拠番号の形式（「甲」「乙」「丙」＋数字、枝番付きを含む）に
合致するかを判定する。このツールはファイルの中身をプレビューできずファイル名の
適否を判断できないため、ファイル名の自動生成・編集機能は持たない（利用者が
あらかじめ適切なファイル名を付けている前提）。判定結果は、4.7（証拠番号処理：
右上スタンプ・枝番ファイル結合）の適用候補になるかどうかの判断材料としてのみ用いる。

根拠：
- 符号・半角3桁数字・被告複数時の添字（乙A等）：準備の手引29頁
  「ファイル名：証拠番号（半角三桁）に加えて...標目のとおり記載（英数字は半角）
  例）「甲001 売買契約書原本」、「乙A001 領収書」」（一次資料の原文で明確に半角と指定）。
- 枝番の表記揺れ（ハイフン／アンダースコア、範囲の「~」）：
  準備の手引29頁の入力例「甲001-1~20」とFAQの「甲001_1～3」が併存
  （docs/02_constraints-report.md 補遺4(4)）。ツールは両表記を受理する。

添字は一次資料上は半角が正式だが、全角で入力されたファイルを誤って
「証拠番号処理の対象外」と判定してしまわないよう、判定自体は全角も
寛容に受理する（ただし本ツールが自動生成する際は半角を用いる）。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path

from mints_prep.config import EvidenceNumberConfig
from mints_prep.models import EvidenceNumberMatch, EvidenceSequenceFinding

# 半角英字が正式（準備の手引29頁「英数字は半角」）。全角も判定上は寛容に受理する。
_SUBLETTER_CHARS = "A-Za-zＡ-Ｚａ-ｚ"
# 枝番範囲の区切りは半角チルダ（U+007E）・波ダッシュ（U+301C）・全角チルダ
# （U+FF5E）のいずれも許容する。Windows IMEの「～」はU+FF5Eであり、FAQ記載の
# 「甲001_1～3」を実際に受理するにはU+FF5Eが必須（U+301Cだけでは取りこぼす）。
_RANGE_SEP = "[~〜～]"
# 枝番の区切りも、数字・添字と同様に全角（－ U+FF0D／＿ U+FF3F）を寛容に受理
# する（「甲００１－１」を枝番なし・標目「－１」と誤解析すると、「甲001-1」との
# 実質重複を見逃す）。
_BRANCH_SEP = "[-_－＿]"
_BRANCH_SEP_CHARS = "-_－＿"
# 証拠番号部分と標目の間の区切り（枝番区切りと同じ文字集合＋空白）。ここに
# 一致した部分は標目そのものではなく区切りとして扱う（title_separatorへ）。
_TITLE_SEPARATOR = re.compile(rf"[{_BRANCH_SEP_CHARS}\s]*")


def _build_pattern(config: EvidenceNumberConfig) -> re.Pattern[str]:
    symbols = "|".join(re.escape(s) for s in config.symbols)
    # number直後の(?!\d)は、規定桁数を超える数字列（日付入りファイル名
    # 「甲20240401_領収書」等）の先頭3桁だけを誤って証拠番号と解釈しない
    # ための数字境界（branch_from/branch_toは\d+が貪欲に数字を取り尽くす
    # ため、後置の境界は不要）。
    return re.compile(
        rf"^(?P<symbol>{symbols})"
        rf"(?P<subletter>[{_SUBLETTER_CHARS}]?)"
        rf"(?P<number>\d{{{config.digits}}})(?!\d)"
        rf"(?:{_BRANCH_SEP}(?P<branch_from>\d+)(?:{_RANGE_SEP}(?P<branch_to>\d+))?)?"
    )


def match_evidence_number(
    filename: str, config: EvidenceNumberConfig
) -> EvidenceNumberMatch | None:
    """ファイル名（拡張子込み・拡張子なしいずれも可）の先頭が証拠番号パターンに
    合致するか判定する。合致しない場合は None を返す。"""
    stem = Path(filename).stem
    pattern = _build_pattern(config)
    m = pattern.match(stem)
    if not m:
        return None

    # 証拠番号部分と標目の間の区切り文字（空白、又は枝番として認識されなかった
    # "-"/"_"等。例：「甲001_売買契約書」の"_"は、直後が数字でなく枝番不成立の
    # ため証拠番号部分には取り込まれないが、標目との区切りとして利用者が使った
    # ものである）は、標目そのものではないため取り除いて別途保持する（title_
    # separator）。ツールはファイル名の内容の適否を判断できない（4.3節）ため、
    # ここで判定した区切り文字は破棄せず、枝番ファイル結合時のファイル名生成
    # （processors/evidence_merger.py）で利用者の元の表記のまま再利用する。
    tail = stem[m.end():]
    sep_match = _TITLE_SEPARATOR.match(tail)
    separator = sep_match.group(0) if sep_match else ""
    title = tail[len(separator):].rstrip()
    return EvidenceNumberMatch(
        symbol=m.group("symbol"),
        subletter=m.group("subletter") or "",
        number=m.group("number"),
        branch_from=m.group("branch_from"),
        branch_to=m.group("branch_to"),
        title=title,
        title_separator=separator,
    )


def check_evidence_sequence(
    filenames: Iterable[str], config: EvidenceNumberConfig
) -> tuple[EvidenceSequenceFinding, ...]:
    """フォルダ内の証拠ファイル群の証拠番号を符号ごとに集計し、欠番・重複を
    検出する（要件定義書4.7「号証の連番・欠番チェック」）。

    証拠番号パターンに合致しないファイル名は無視する。符号（甲/乙/丙、乙A等）
    ごとに独立した連番系列として扱い、枝番（甲001-1〜3等）は親番号単位で1つと
    数える。欠番は観測された最大番号までの範囲（1〜最大）のうち出現しない番号、
    重複は同一の符号・番号・枝番の組み合わせが複数ファイルに存在する場合、及び
    親番号そのもの（甲001）と枝番付き（甲001-1等）が併存する場合を指す
    （通し番号の異なる枝番同士は正当な結合対象であり重複とはしない）。
    警告のみで、自動採番の押し付けはしない。問題が見つかった符号系列のみを
    返す（欠番・重複のいずれも無い符号系列は含まない）。
    """
    groups: dict[str, list[EvidenceNumberMatch]] = {}
    for filename in filenames:
        match = match_evidence_number(filename, config)
        if match is None:
            continue
        # 添字はNFKC正規化＋大文字化して系列キーにする（判定パターンは全角
        # 添字も寛容に受理するため、生文字列のままだと「乙A001」と「乙Ａ001」が
        # 別系列扱いになり、実質重複・欠番の連鎖を見逃す。数字を数値へ正規化
        # しているのと同じ趣旨）。
        subletter_key = unicodedata.normalize("NFKC", match.subletter).upper()
        symbol_key = f"{match.symbol}{subletter_key}"
        groups.setdefault(symbol_key, []).append(match)

    def _symbol_order(symbol_key: str) -> tuple[int, str]:
        # 表示順は慣用の符号順（config.symbolsの並び＝甲・乙・丙…）に合わせる。
        # 単純なsorted()はUnicodeコードポイント順（丙<乙<甲）となり慣用と
        # 逆順になるため使わない。添字付き（乙A等）は符号本体の順→添字順。
        for i, symbol in enumerate(config.symbols):
            if symbol_key.startswith(symbol):
                return (i, symbol_key[len(symbol):])
        return (len(config.symbols), symbol_key)

    findings: list[EvidenceSequenceFinding] = []
    for symbol_key in sorted(groups, key=_symbol_order):
        matches = groups[symbol_key]
        numbers = sorted({int(m.number) for m in matches})
        missing = (
            tuple(n for n in range(1, numbers[-1] + 1) if n not in numbers) if numbers else ()
        )

        # 重複判定のキーは数値へ正規化する（判定パターンは全角数字も寛容に
        # 受理するため、文字列のままだと「甲001」と「甲００１」の併存や
        # 「-1」と「_01」等の表記揺れの重複を見逃す）。枝番範囲（1~3等）は
        # 展開し、範囲同士・範囲と単独枝番の重なりも重複として検出する。
        occurrence_counts: dict[tuple[int, int | None], int] = {}
        for m in matches:
            number = int(m.number)
            if m.branch_from is None:
                branch_keys: list[int | None] = [None]
            else:
                branch_from = int(m.branch_from)
                branch_to = int(m.branch_to) if m.branch_to is not None else branch_from
                branch_keys = list(range(branch_from, max(branch_from, branch_to) + 1))
            for branch in branch_keys:
                key = (number, branch)
                occurrence_counts[key] = occurrence_counts.get(key, 0) + 1
        # 親番号そのもの（甲001）と枝番付き（甲001-1等）が併存する場合も、
        # 同一号証の提出単位が衝突しているため重複として扱う。
        numbers_as_parent = {n for (n, b) in occurrence_counts if b is None}
        numbers_as_branch = {n for (n, b) in occurrence_counts if b is not None}
        parent_branch_conflicts = numbers_as_parent & numbers_as_branch
        duplicate_numbers = tuple(
            sorted(
                {number for (number, _branch), count in occurrence_counts.items() if count >= 2}
                | parent_branch_conflicts
            )
        )

        if missing or duplicate_numbers:
            findings.append(
                EvidenceSequenceFinding(
                    symbol_key=symbol_key,
                    missing_numbers=missing,
                    duplicate_numbers=duplicate_numbers,
                )
            )
    return tuple(findings)
