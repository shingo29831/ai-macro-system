# @role: ローマ字入力をひらがなに変換し、OCRテキストフィールド特定のための検索文字列を生成する。

_ROMAJI_MAP = {
    'a': 'あ', 'i': 'い', 'u': 'う', 'e': 'え', 'o': 'お',
    'ka': 'か', 'ki': 'き', 'ku': 'く', 'ke': 'け', 'ko': 'こ',
    'sa': 'さ', 'shi': 'し','si': 'し', 'su': 'す', 'se': 'せ', 'so': 'そ',
    'ta': 'た', 'chi': 'ち','ti': 'ち', 'tsu': 'つ','tu': 'つ', 'te': 'て', 'to': 'と',
    'na': 'な', 'ni': 'に', 'nu': 'ぬ', 'ne': 'ね', 'no': 'の',
    'ha': 'は', 'hi': 'ひ', 'fu': 'ふ','hu': 'ふ', 'he': 'へ', 'ho': 'ほ',
    'ma': 'ま', 'mi': 'み', 'mu': 'む', 'me': 'め', 'mo': 'も',
    'ya': 'や', 'yu': 'ゆ', 'yo': 'よ',
    'ra': 'ら', 'ri': 'り', 'ru': 'る', 're': 'れ', 'ro': 'ろ',
    'wa': 'わ', 'wo': 'を', 'nn': 'ん', 'n': 'ん',
    'ga': 'が', 'gi': 'ぎ', 'gu': 'ぐ', 'ge': 'げ', 'go': 'ご',
    'za': 'ざ', 'ji': 'じ','zi': 'じ', 'zu': 'ず', 'ze': 'ぜ', 'zo': 'ぞ',
    'da': 'だ', 'di': 'ぢ', 'du': 'づ', 'de': 'で', 'do': 'ど',
    'ba': 'ば', 'bi': 'び', 'bu': 'ぶ', 'be': 'べ', 'bo': 'ぼ',
    'pa': 'ぱ', 'pi': 'ぴ', 'pu': 'ぷ', 'pe': 'ぺ', 'po': 'ぽ',
    '-': 'ー'
}

def to_hiragana(romaji_text: str) -> str:
    """
    入力途中のローマ字バッファ(例: 'nagoya')を画面上の文字列(例: 'なごや')に変換する。
    """
    if not romaji_text:
        return ""
        
    result = []
    i = 0
    length = len(romaji_text)
    
    while i < length:
        if i + 2 < length and romaji_text[i:i+3] in _ROMAJI_MAP:
            result.append(_ROMAJI_MAP[romaji_text[i:i+3]])
            i += 3
        elif i + 1 < length and romaji_text[i:i+2] in _ROMAJI_MAP:
            result.append(_ROMAJI_MAP[romaji_text[i:i+2]])
            i += 2
        elif i + 1 < length and romaji_text[i] == romaji_text[i+1] and romaji_text[i] not in "aiueon":
            result.append("っ")
            i += 1
        elif romaji_text[i] in _ROMAJI_MAP:
            result.append(_ROMAJI_MAP[romaji_text[i]])
            i += 1
        else:
            result.append(romaji_text[i])
            i += 1
            
    return "".join(result)