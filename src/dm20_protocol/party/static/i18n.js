/**
 * Party Mode interface text in other languages.
 * Keys are the English strings used in index.html and app.js; a missing key
 * falls back to English. Game content (the DM's narrative, item and spell
 * names) is not translated here: the DM writes it in each player's language.
 */
(function () {
    'use strict';

    var zh = {
        // tabs and sections
        'Story': '故事', 'Sheet': '角色', 'Bag': '背包',
        'Abilities': '属性', 'Skills': '技能', 'Spells': '法术', 'Features': '特性',
        'Saving throws': '豁免', 'Features and traits': '特性与专长',
        'Proficiencies and languages': '熟练项与语言',
        'Quest items': '任务物品', 'Equipped': '已装备', 'Pack': '行囊', 'Coin': '钱币',
        'Table options': '桌面选项', 'Session log': '会话记录',
        // story
        'The lamps aren\'t lit yet.': '灯还没有点亮。',
        'The story appears here when the DM begins. You can send an action now.': 'DM 开始后,故事会出现在这里。你现在就可以发送行动。',
        'What do you do?': '你要做什么?', 'Whisper to the DM…': '悄悄告诉 DM…',
        'Waiting for your turn…': '等待你的回合…',
        'The DM': 'DM', 'The DM, only to you': 'DM(仅对你)', 'You': '你', 'You, to the DM': '你(对 DM)',
        'The DM is thinking…': 'DM 正在思考…',
        'Show recent only': '只看最近的', 'Show {n} earlier': '显示更早的 {n} 条',
        'Sending…': '发送中…', 'Sent': '已发送', 'DM is reading': 'DM 正在阅读', 'Answered': '已回应',
        "Didn't send": '未发送',
        'Sent. Waiting for the DM.': '已发送,等待 DM。', 'Whisper sent. Waiting for the DM.': '悄悄话已发送,等待 DM。',
        'The DM is reading your action.': 'DM 正在阅读你的行动。', 'The DM answered.': 'DM 已回应。',
        'Not your turn yet.': '还没轮到你。',
        'Try again': '重试', 'Edit and resend': '修改后重发', 'Go with this': '就这样',
        'Read aloud': '朗读', 'Stop reading': '停止朗读', 'Loading voice…': '正在准备语音…',
        'Read-aloud is not available right now.': '暂时无法朗读。',
        'Listening…': '正在聆听…', 'Tap to speak': '点击说话', 'Tap to stop': '点击停止', 'Send action': '发送行动',
        'Microphone access denied': '麦克风权限被拒绝',
        'Editing your earlier message': '正在修改你之前的消息',
        'The DM had already picked up your earlier message, so this was sent as a new one.': 'DM 已经读到你之前的消息,所以这条作为新消息发送。',
        'Whisper to the DM only': '只悄悄告诉 DM', 'Your action': '你的行动',
        'Open picture full size': '查看大图', 'A picture from the DM': 'DM 发来的图片',
        // menu
        'Language': '语言', 'Text size': '字号', 'Small': '小', 'Normal': '标准', 'Large': '大', 'Larger': '特大',
        'Notify me when the DM replies': 'DM 回复时通知我',
        'On': '开', 'Off': '关', 'Open the map': '打开地图',
        'Connected as {name}': '已连接:{name}',
        'Connected': '已连接', 'Reconnecting…': '重新连接中…', 'Offline': '离线',
        // sheet
        'Strength': '力量', 'Dexterity': '敏捷', 'Constitution': '体质',
        'Intelligence': '智力', 'Wisdom': '感知', 'Charisma': '魅力',
        'Str': '力量', 'Dex': '敏捷', 'Con': '体质', 'Int': '智力', 'Wis': '感知', 'Cha': '魅力',
        'Proficiency': '熟练加值', 'Initiative': '先攻', 'Speed': '速度', 'Passive Perception': '被动察觉',
        'Proficient': '熟练', 'Not proficient': '不熟练',
        '{ability} saving throw': '{ability}豁免',
        'The DM calls for saves. Roll a d20 and add {bonus}.': 'DM 要求豁免时,掷 d20 并加上 {bonus}。',
        'Use {name}': '使用{name}', 'Cast {name}': '施放{name}', 'Use it': '使用',
        'No description recorded. Ask the DM.': '没有记录说明,请询问 DM。',
        'No features yet.': '暂无特性。', 'None recorded.': '暂无记录。', 'Your pack is empty.': '你的行囊是空的。',
        'Languages': '语言', 'Tools and instruments': '工具与乐器', 'Armor and weapons': '护甲与武器',
        'Main hand': '主手', 'Off hand': '副手', 'Armor': '护甲', 'Shield': '盾牌', 'Nothing': '无',
        'Concentration': '专注', 'Ritual': '仪式', 'Heroic Inspiration': '英雄激励', 'Inspired': '激励',
        'Ready': '可用', 'Ready to use': '可以使用',
        // skills
        'Acrobatics': '特技', 'Animal Handling': '驯兽', 'Arcana': '奥秘', 'Athletics': '运动',
        'Deception': '欺瞒', 'History': '历史', 'Insight': '洞悉', 'Intimidation': '威吓',
        'Investigation': '调查', 'Medicine': '医药', 'Nature': '自然', 'Perception': '察觉',
        'Performance': '表演', 'Persuasion': '游说', 'Religion': '宗教', 'Sleight of Hand': '巧手',
        'Stealth': '隐匿', 'Survival': '求生',
        'Notice things: sounds, movement, something out of place.': '留意周围:声音、动静、不对劲的地方。',
        'Read a person: are they lying, scared, hiding something?': '看穿一个人:是否在说谎、害怕或有所隐瞒?',
        'Search and reason: find clues, work out how something works.': '搜索与推理:寻找线索,弄清事物的原理。',
        'Move unseen and unheard.': '悄无声息地行动。',
        'Pick a pocket, palm an object, plant something unnoticed.': '扒窃、藏物,或不被察觉地放置东西。',
        'Convince someone honestly, with charm or good reasons.': '以魅力或道理真诚地说服他人。',
        'Mislead someone with a lie, a disguise or a bluff.': '用谎言、伪装或虚张声势误导他人。',
        'Climb, jump, swim, shove or grapple.': '攀爬、跳跃、游泳、推撞或擒抱。',
        'Keep your balance, tumble, slip free.': '保持平衡、翻滚、挣脱。',
        'Entertain a crowd: music, story, dance.': '用音乐、故事或舞蹈娱乐众人。',
        'Get your way through threats or a hard stare.': '用威胁或凌厉的目光达到目的。',
        'Track, find your way, read the weather and the wild.': '追踪、辨路,观察天气与荒野。',
        'Know about magic, spells and magical things.': '了解魔法、法术与魔法事物。',
        'Recall people, places and events of the past.': '回忆过去的人物、地点与事件。',
        'Know plants, animals and the land.': '了解植物、动物与大地。',
        'Know gods, rites and holy symbols.': '了解神祇、仪式与圣徽。',
        'Steady the dying, judge an illness or wound.': '稳住濒死者,判断疾病或伤势。',
        'Calm, read or guide an animal.': '安抚、理解或引导动物。',
        // combat
        'Combat ended': '战斗结束', 'Everyone acts at once.': '所有人同时行动。', 'Time’s up': '时间到',
    };

    // Traditional Chinese: same keys. Built from the Simplified text with the
    // characters that differ replaced.
    var S2T = {
        '属': '屬', '术': '術', '练': '練', '项': '項', '语': '語', '任': '任', '务': '務', '装': '裝', '备': '備',
        '钱': '錢', '币': '幣', '选': '選', '会': '會', '话': '話', '记': '記', '录': '錄', '灯': '燈', '还': '還',
        '没': '沒', '点': '點', '开': '開', '后': '後', '现': '現', '这': '這', '里': '裡', '发': '發', '动': '動',
        '么': '麼', '诉': '訴', '轮': '輪', '对': '對', '仅': '僅', '显': '顯', '条': '條', '读': '讀', '应': '應',
        '阅': '閱', '试': '試', '样': '樣', '备语': '備語', '准': '準', '时': '時', '无': '無', '法': '法',
        '聆': '聆', '听': '聽', '说': '說', '图': '圖', '来': '來', '号': '號', '标': '標', '自': '自', '复': '覆',
        '关': '關', '连': '連', '线': '線', '离': '離', '质': '質', '体': '體', '魅': '魅', '觉': '覺', '豁': '豁',
        '掷': '擲', '并': '並', '请': '請', '询': '詢', '问': '問', '暂': '暫', '护': '護', '盾': '盾', '专': '專',
        '仪': '儀', '励': '勵', '驯': '馴', '兽': '獸', '奥': '奧', '运': '運', '欺': '欺', '瞒': '瞞', '历': '歷',
        '吓': '嚇', '调': '調', '医': '醫', '药': '藥', '游': '遊', '隐': '隱', '围': '圍', '声': '聲', '静': '靜',
        '劲': '勁', '个': '個', '谎': '謊', '线索': '線索', '弄': '弄', '悄': '悄', '窃': '竊', '藏': '藏',
        '诚': '誠', '误': '誤', '导': '導', '伪': '偽', '虚': '虛', '张': '張', '势': '勢', '爬': '爬', '跃': '躍',
        '擒': '擒', '衡': '衡', '滚': '滾', '挣': '掙', '脱': '脫', '乐': '樂', '娱': '娛', '众': '眾', '胁': '脅',
        '凌': '凌', '厉': '厲', '达': '達', '踪': '蹤', '辨': '辨', '观': '觀', '气': '氣', '与': '與', '识': '識',
        '忆': '憶', '过': '過', '点与': '點與', '植': '植', '徽': '徽', '稳': '穩', '濒': '瀕', '断': '斷',
        '伤': '傷', '抚': '撫', '战': '戰', '斗': '鬥', '结': '結', '间': '間', '为': '為', '双': '雙',
        '搜': '搜', '寻': '尋', '东': '東', '置': '置', '给': '給', '查': '查', '单': '單', '当': '當',
        '解': '解', '场': '場', '强': '強', '处': '處', '经': '經', '义': '義', '风': '風', '门': '門',
    };
    var zhHant = {};
    Object.keys(zh).forEach(function (key) {
        zhHant[key] = zh[key].replace(/[一-鿿]/g, function (ch) { return S2T[ch] || ch; });
    });
    zhHant['Bag'] = '背包';
    zhHant['Language'] = '語言';

    window.PARTY_I18N = { zh: zh, 'zh-Hant': zhHant };
    window.PARTY_LANGUAGE_NAMES = { en: 'English', zh: '简体中文', 'zh-Hant': '繁體中文' };
})();
