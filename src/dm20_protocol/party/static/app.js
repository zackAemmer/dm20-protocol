/**
 * Party Mode player UI — WebSocket client and DOM controller.
 *
 * Three views: Story (feed + action bar), Sheet, Bag. Vitals (HP, AC,
 * conditions, spell slots, Heroic Inspiration) stay pinned on top.
 *
 * No external dependencies. Vanilla JavaScript only.
 */

(function () {
    'use strict';

    // ===== Configuration =====
    var config = window.PARTY_CONFIG || {};
    var TOKEN = config.token || '';
    var PLAYER_ID = config.playerId || '';
    var PLAYER_NAME = config.playerName || PLAYER_ID;
    var VAPID_PUBLIC_KEY = config.vapidPublicKey || '';
    var WS_PROTOCOL = (window.location.protocol === 'https:') ? 'wss:' : 'ws:';
    var WS_URL = WS_PROTOCOL + '//' + window.location.host + '/ws?token=' + TOKEN;
    var API_BASE = window.location.origin;

    var RECONNECT_BASE_MS = 1000;
    var RECONNECT_MAX_MS = 30000;
    var HEARTBEAT_INTERVAL_MS = 30000;
    var FEED_VISIBLE_COUNT = 30;

    // ===== State =====
    var ws = null;
    var reconnectAttempt = 0;
    var reconnectTimer = null;
    var heartbeatTimer = null;
    var lastSeenTimestamp = null;
    var renderedMsgIds = {};
    var connectedAnnounced = false;
    var lastSystemText = null;
    var feedShowAll = false;
    var feedToggleEl = null;
    var isPrivateMode = false;
    var activeTab = 'game';
    var cachedCharacterData = null;
    var pendingActionId = null;
    var actionCards = {};      // action_id -> { el, frame }
    var linkTarget = null;     // { action_id, name }
    var inputGated = false;
    var sending = false;

    var audioContext = null;
    var audioChunkBuffers = {};
    var audioPlaybackQueue = [];
    var isPlayingAudio = false;
    var currentAudioSource = null;
    var speakRequests = {};    // response_id -> the speaker button waiting for audio
    var playingButton = null;
    var readAloudAvailable = true;

    function stored(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }
    function store(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* storage blocked */ } }

    var TEXT_SIZES = ['Normal', 'Large', 'Larger'];
    var textSize = Math.max(0, TEXT_SIZES.indexOf(stored('textSize') || 'Normal'));

    // ===== Language =====
    var I18N = window.PARTY_I18N || {};
    var LANGUAGE_NAMES = window.PARTY_LANGUAGE_NAMES || { en: 'English' };
    var LANGS = Object.keys(LANGUAGE_NAMES);
    var langChosen = stored('lang');           // null until the player picks one on this device
    var LANG = langChosen || guessLanguage();

    function guessLanguage() {
        var n = (navigator.language || 'en').toLowerCase();
        if (n.indexOf('zh') === 0) return /tw|hk|mo|hant/.test(n) ? 'zh-Hant' : 'zh';
        return 'en';
    }

    // t('Sent') or t('Show {n} earlier', { n: 3 }); unknown text passes through.
    function t(text, vars) {
        var dict = I18N[LANG];
        var out = (dict && dict[text]) || text;
        if (vars) Object.keys(vars).forEach(function (k) { out = out.replace('{' + k + '}', vars[k]); });
        return out;
    }

    function applyStaticText() {
        document.documentElement.lang = LANG === 'zh' ? 'zh-Hans' : LANG;
        $$('[data-i18n]').forEach(function (n) { n.textContent = t(n.getAttribute('data-i18n')); });
        $$('[data-i18n-placeholder]').forEach(function (n) { n.placeholder = t(n.getAttribute('data-i18n-placeholder')); });
        $$('[data-i18n-label]').forEach(function (n) {
            var v = t(n.getAttribute('data-i18n-label'));
            n.setAttribute('aria-label', v);
            if (n.hasAttribute('title')) n.title = v;
        });
    }

    function applyTextSize() {
        document.documentElement.setAttribute('data-text-size', TEXT_SIZES[textSize].toLowerCase());
    }

    // ===== DOM =====
    var $ = function (sel) { return document.querySelector(sel); };
    var $$ = function (sel) { return document.querySelectorAll(sel); };

    var dom = {
        connectionDot: $('.connection-dot'),
        feed: $('.narrative-feed'),
        emptyState: $('#empty-state'),
        systemMessages: $('.system-section__messages'),
        actionBar: $('.action-bar'),
        actionInput: $('.action-bar__input'),
        actionSend: $('.action-bar__send'),
        actionStatus: $('.action-bar__status'),
        actionForm: $('.action-bar__form'),
        privateBtn: $('.action-bar__private-toggle'),
        linkChip: $('#link-chip'),
        sub: $('#vitals-sub'),
        hp: $('.hp'),
        hpBlocks: $('.hp__blocks'),
        hpText: $('.hp__text'),
        acValue: $('#stat-ac'),
        inspPill: $('#insp-pill'),
        vitalsSlots: $('#vitals-slots'),
        conditions: $('.header__conditions'),
        langBtn: $('#lang-btn'),
        textSizeBtn: $('#text-size-btn'),
        notifyBtn: $('#notify-btn'),
        combatBanner: $('.combat-banner'),
        combatInfo: $('.combat-banner__info'),
        initiativeList: $('.initiative-list'),
        abilityGrid: $('.ability-grid'),
        sheetFacts: $('#sheet-facts'),
        savingThrowsList: $('.saving-throws-list'),
        skillsList: $('.skills-list'),
        featuresList: $('.features-list'),
        proficienciesSummary: $('.proficiencies-summary'),
        spellcastingHeader: $('.spellcasting-header'),
        spellSlots: $('.spell-slots'),
        spellsKnownList: $('.spells-known-list'),
        spellsSection: $('#sec-spells'),
        jumpSpells: $('#jump-spells'),
        questSection: $('#quest-section'),
        questList: $('.quest-list'),
        equipmentSlots: $('.equipment-slots'),
        inventoryList: $('.inventory-list'),
        currencyDisplay: $('.currency-display'),
        currencySection: $('#currency-section'),
        detail: $('#detail'),
        menu: $('#menu'),
        lightbox: $('#lightbox'),
        storyBadge: $('.tab-bar__btn[data-tab="game"] .tab-bar__badge'),
    };


    // ===== Small helpers =====

    function escapeHtml(text) {
        if (text === null || text === undefined) return '';
        var div = document.createElement('div');
        div.textContent = String(text);
        return div.innerHTML;
    }

    // Inline markdown: escape first, then apply bold/italic. Safe from XSS.
    function inlineMd(text) {
        var s = escapeHtml(text);
        s = s.replace(/\*\*([\s\S]+?)\*\*/g, '<strong>$1</strong>');
        s = s.replace(/(^|[^*])\*([^*\n]+?)\*/g, '$1<em>$2</em>');
        s = s.replace(/(^|\W)_([^_\n]+?)_(?=\W|$)/g, '$1<em>$2</em>');
        return s;
    }

    function renderMarkdown(text) {
        if (!text) return '';
        return inlineMd(text).replace(/\n/g, '<br>');
    }

    function paragraphs(text) {
        if (!text) return '';
        return String(text).split(/\n{2,}|\n/).filter(function (p) { return p.trim(); })
            .map(function (p) { return '<p>' + inlineMd(p.trim()) + '</p>'; }).join('');
    }

    function formatTime(timestamp) {
        if (!timestamp) return '';
        var d = new Date(timestamp);
        if (isNaN(d.getTime())) return '';
        return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    }

    function formatMod(mod) { return mod >= 0 ? '+' + mod : '' + mod; }

    function el(tag, cls, html) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (html !== undefined) e.innerHTML = html;
        return e;
    }


    // ===== Tabs =====

    function switchTab(tabName) {
        if (tabName === activeTab) return;
        activeTab = tabName;
        $$('.tab-content').forEach(function (c) {
            c.classList.toggle('tab-content--active', c.dataset.tab === tabName);
        });
        $$('.tab-bar__btn').forEach(function (b) {
            var on = b.dataset.tab === tabName;
            b.classList.toggle('tab-bar__btn--active', on);
            b.setAttribute('aria-current', on ? 'page' : 'false');
        });
        if (tabName === 'game') {
            if (dom.storyBadge) dom.storyBadge.hidden = true;
            scrollFeedToBottom();
        }
    }

    function flagStory() {
        if (activeTab !== 'game' && dom.storyBadge) dom.storyBadge.hidden = false;
    }


    // ===== WebSocket =====

    function connect() {
        if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;
        updateConnectionStatus('reconnecting');
        ws = new WebSocket(WS_URL);

        ws.onopen = function () {
            reconnectAttempt = 0;
            updateConnectionStatus('connected');
            startHeartbeat();
            // Ask for history on every (re)connect: first load gets the whole
            // backlog, a reconnect only what was missed. Ids dedup any overlap.
            ws.send(JSON.stringify({ type: 'history_request', since: lastSeenTimestamp || '' }));
        };
        ws.onmessage = function (event) {
            try { handleMessage(JSON.parse(event.data)); }
            catch (e) { console.error('Failed to handle message:', e); }
        };
        ws.onclose = function () {
            stopHeartbeat();
            updateConnectionStatus('disconnected');
            scheduleReconnect();
        };
        ws.onerror = function (err) { console.error('WebSocket error:', err); };
    }

    function scheduleReconnect() {
        if (reconnectTimer) clearTimeout(reconnectTimer);
        var delay = Math.min(RECONNECT_BASE_MS * Math.pow(2, reconnectAttempt), RECONNECT_MAX_MS);
        reconnectAttempt++;
        updateConnectionStatus('reconnecting');
        reconnectTimer = setTimeout(connect, delay);
    }

    function startHeartbeat() {
        stopHeartbeat();
        heartbeatTimer = setInterval(function () {
            if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'heartbeat' }));
        }, HEARTBEAT_INTERVAL_MS);
    }

    function stopHeartbeat() {
        if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null; }
    }

    function updateConnectionStatus(status) {
        if (!dom.connectionDot) return;
        dom.connectionDot.className = 'connection-dot';
        if (status === 'connected') dom.connectionDot.classList.add('connection-dot--connected');
        else if (status === 'reconnecting') dom.connectionDot.classList.add('connection-dot--reconnecting');
        dom.connectionDot.title = t(status === 'connected' ? 'Connected' :
            (status === 'reconnecting' ? 'Reconnecting…' : 'Offline'));
    }


    // ===== Message router =====

    function handleMessage(msg) {
        // Only real feed content moves the history cursor, and only forward.
        if (msg.timestamp && (msg.type === 'narrative' || msg.type === 'private')) {
            if (!lastSeenTimestamp || msg.timestamp > lastSeenTimestamp) lastSeenTimestamp = msg.timestamp;
        }

        switch (msg.type) {
            case 'connected':
                setDmPresence(msg.dm_last_active);
                if (!connectedAnnounced) {
                    connectedAnnounced = true;
                    addSystemMessage(t('Connected as {name}', { name: PLAYER_NAME }));
                }
                readAloudAvailable = msg.read_aloud_available !== false;
                syncLanguageWithServer(msg.language);
                fetchCharacter();
                break;
            case 'language':
                if (msg.language && msg.language !== LANG) setLanguage(msg.language, false);
                break;
            case 'audio_unavailable':
                audioFailed(msg.response_id);
                break;
            case 'narrative':
                var isNew = !!(msg.id && !renderedMsgIds[msg.id]);
                addNarrativeMessage(msg);
                if (isNew) notifyNew(msg, msg.content || msg.narrative || (msg.image && msg.image.caption), false);
                break;
            case 'private':
                // The server folds narrative + whisper into one 'private' frame.
                hideThinkingIndicator();
                addNarrativeMessage(msg);
                addPrivateMessage(msg);
                notifyNew(msg, msg.private || msg.content || msg.narrative, true);
                break;
            case 'player_action':
                renderActionFrame(msg);
                break;
            case 'thinking':
                showThinkingIndicator(msg.message);
                break;
            case 'dm_presence':
                setDmPresence(msg.last_active);
                break;
            case 'character_update':
                applyCharacter(msg.data);
                break;
            case 'character_changed':
                scheduleCharacterRefresh(400);
                break;
            case 'combat_state':
                updateCombatState(msg.data);
                break;
            case 'action_status':
                updateActionStatus(msg.action_id, msg.status, msg.error);
                break;
            case 'system':
                addSystemMessage(msg.content);
                break;
            case 'audio':
                handleAudioChunk(msg);
                break;
            case 'ping':
                if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'pong' }));
                break;
            default:
                console.log('Unknown message type:', msg.type, msg);
        }
    }


    // ===== Feed =====

    function isFeedNearBottom() {
        var f = dom.feed;
        return !f || f.scrollHeight - f.scrollTop - f.clientHeight < 140;
    }

    function scrollFeedToBottom() {
        if (dom.feed) dom.feed.scrollTop = dom.feed.scrollHeight;
    }

    var thinkingEl = null;

    // Insert by timestamp so history replay, live frames and local cards
    // all land in story order.
    function placeInFeed(node, ts) {
        if (!dom.feed) return;
        if (dom.emptyState) dom.emptyState.hidden = true;
        var stick = isFeedNearBottom();
        node.dataset.ts = ts || new Date().toISOString();
        var items = dom.feed.querySelectorAll('[data-ts]');
        var before = null;
        for (var i = items.length - 1; i >= 0; i--) {
            if (items[i] === node) continue;
            if (items[i].dataset.ts <= node.dataset.ts) break;
            before = items[i];
        }
        if (before) dom.feed.insertBefore(node, before);
        else if (thinkingEl && thinkingEl.parentNode === dom.feed) dom.feed.insertBefore(node, thinkingEl);
        else dom.feed.appendChild(node);
        updateFeedVisibility();
        if (stick) scrollFeedToBottom();
        flagStory();
    }

    function ensureFeedToggle() {
        if (feedToggleEl || !dom.feed) return;
        feedToggleEl = el('button', 'feed-toggle');
        feedToggleEl.type = 'button';
        feedToggleEl.addEventListener('click', function () {
            feedShowAll = !feedShowAll;
            updateFeedVisibility();
        });
        dom.feed.insertBefore(feedToggleEl, dom.feed.firstChild);
    }

    function updateFeedVisibility() {
        if (!dom.feed) return;
        var items = dom.feed.querySelectorAll('[data-ts]');
        var total = items.length;
        var hiddenCount = feedShowAll ? 0 : Math.max(0, total - FEED_VISIBLE_COUNT);
        for (var i = 0; i < total; i++) items[i].hidden = i < hiddenCount;
        if (hiddenCount > 0) {
            ensureFeedToggle();
            feedToggleEl.hidden = false;
            feedToggleEl.textContent = t('Show {n} earlier', { n: hiddenCount });
        } else if (feedToggleEl) {
            feedToggleEl.hidden = !(feedShowAll && total > FEED_VISIBLE_COUNT);
            feedToggleEl.textContent = t('Show recent only');
        }
    }

    function showThinkingIndicator(message) {
        hideThinkingIndicator();
        thinkingEl = el('div', 'message message--thinking',
            '<span class="thinking-dots" aria-hidden="true"><i></i><i></i><i></i></span>' +
            '<span>' + escapeHtml(message || t('The DM is thinking…')) + '</span>');
        var stick = isFeedNearBottom();
        if (dom.feed) dom.feed.appendChild(thinkingEl);
        if (stick) scrollFeedToBottom();
        if (pendingActionId) setActionStatus('The DM is thinking…', 'processing');
    }

    function hideThinkingIndicator() {
        if (thinkingEl && thinkingEl.parentNode) thinkingEl.parentNode.removeChild(thinkingEl);
        thinkingEl = null;
    }

    // DM narration follows the table's conventions (see the DM playbook):
    // *scene text* in italics, **Name**: "speech", (Perception check: 12 + 3 = 15),
    // [Round 2 -- Goblin Archer].
    var CHECK_RE = /^[(\uFF08]([^()\uFF08\uFF09]*?(check|save|saving throw|attack|initiative|damage|roll|throw|\u68C0\u5B9A|\u6AA2\u5B9A|\u8C41\u514D|\u653B\u51FB|\u653B\u64CA|\u5148\u653B|\u4F24\u5BB3|\u50B7\u5BB3|\u63B7\u9AB0|\u64F2\u9AB0)[^()\uFF08\uFF09]*?)[)\uFF09]\s*(.*)$/i;

    function renderStory(text) {
        var out = [];
        String(text || '').split(/\n+/).forEach(function (raw) {
            var line = raw.trim();
            if (!line) return;
            var m;
            if ((m = line.match(/^\*([^*][\s\S]*[^*]|[^*])\*$/)) && m[1].indexOf('**') === -1) {
                out.push('<p class="story__scene">' + inlineMd(m[1]) + '</p>');
            } else if ((m = line.match(/^_([^_][\s\S]*)_$/))) {
                out.push('<p class="story__scene">' + inlineMd(m[1]) + '</p>');
            } else if ((m = line.match(/^\*\*([^*]{1,60})\*\*\s*[:\uFF1A]\s*([\s\S]+)$/))) {
                out.push('<p class="story__npc"><span class="story__speaker">' + escapeHtml(m[1]) +
                    '</span>' + inlineMd(m[2]) + '</p>');
            } else if ((m = line.match(CHECK_RE))) {
                out.push('<p class="story__check">' + escapeHtml(m[1]) + '</p>');
                if (m[3]) out.push('<p>' + inlineMd(m[3]) + '</p>');
            } else if ((m = line.match(/^[\[\u3010]([^\]\u3011]+)[\]\u3011]\s*([\s\S]*)$/))) {
                var rest = m[2];
                var chk = rest.match(CHECK_RE);
                out.push('<p class="story__combat">' + escapeHtml(m[1]) + '</p>');
                if (chk) {
                    out.push('<p class="story__check">' + escapeHtml(chk[1]) + '</p>');
                    if (chk[3]) out.push('<p>' + inlineMd(chk[3]) + '</p>');
                } else if (rest) {
                    out.push('<p>' + inlineMd(rest) + '</p>');
                }
            } else {
                out.push('<p>' + inlineMd(line) + '</p>');
            }
        });
        return out.join('');
    }

    function imageFigure(image) {
        if (!image || !image.file) return null;
        var fig = el('figure', 'scene');
        var btn = el('button', 'scene__frame');
        btn.type = 'button';
        btn.setAttribute('aria-label', t('Open picture full size'));
        var img = document.createElement('img');
        img.src = '/img/' + encodeURIComponent(image.file) + '?token=' + encodeURIComponent(TOKEN);
        img.alt = image.caption || t('A picture from the DM');
        img.decoding = 'async';
        if (image.width && image.height) {
            img.width = image.width;
            img.height = image.height;
        }
        img.addEventListener('load', function () {
            if (isFeedNearBottom()) scrollFeedToBottom();
        });
        btn.appendChild(img);
        btn.addEventListener('click', function () { openLightbox(img.src, image.caption); });
        fig.appendChild(btn);
        if (image.caption) fig.appendChild(el('figcaption', '', escapeHtml(image.caption)));
        return fig;
    }

    function addNarrativeMessage(msg) {
        hideThinkingIndicator();
        var text = ((msg.content || msg.narrative || '') + '');
        if (!text.trim() && !msg.image) return;
        if (msg.id) {
            if (renderedMsgIds[msg.id]) return;
            renderedMsgIds[msg.id] = true;
        }
        var card = el('article', 'message message--narrative');
        var meta = el('div', 'message__meta',
            '<span class="message__who">' + escapeHtml(t('The DM')) + '</span><time>' + formatTime(msg.timestamp) + '</time>');
        if (text.trim() && msg.id && readAloudAvailable) meta.appendChild(speakButton(msg.id));
        card.appendChild(meta);
        var fig = imageFigure(msg.image);
        if (fig) card.appendChild(fig);
        if (text.trim()) card.appendChild(el('div', 'story', renderStory(text)));
        placeInFeed(card, msg.timestamp);
        if (msg.action_id) updateActionStatus(msg.action_id, 'resolved');
        (msg.action_ids || []).forEach(function (id) { updateActionStatus(id, 'resolved'); });
    }

    function addPrivateMessage(msg) {
        var text = msg.private || msg.content || '';
        if (!String(text).trim()) return;
        var key = (msg.id || '') + ':private';
        if (msg.id) {
            if (renderedMsgIds[key]) return;
            renderedMsgIds[key] = true;
        }
        var card = el('article', 'message message--private message--private-new');
        card.appendChild(el('div', 'message__meta',
            '<span class="message__who">' + escapeHtml(t('The DM, only to you')) + '</span><time>' + formatTime(msg.timestamp) + '</time>'));
        card.appendChild(el('div', 'story', renderStory(text)));
        // A hair after the public part of the same reply.
        placeInFeed(card, (msg.timestamp || new Date().toISOString()) + '~');
        setTimeout(function () { card.classList.remove('message--private-new'); }, 600);
    }

    function addSystemMessage(content) {
        if (!content || content === lastSystemText) return;
        lastSystemText = content;
        if (!dom.systemMessages) return;
        var row = el('div', 'message message--system', escapeHtml(content));
        dom.systemMessages.insertBefore(row, dom.systemMessages.firstChild);
        while (dom.systemMessages.children.length > 60) dom.systemMessages.removeChild(dom.systemMessages.lastChild);
    }


    // ===== Player actions in the feed =====

    var STATUS_TEXT = {
        sending: 'Sending…',
        pending: 'Sent',
        processing: 'DM is reading',
        resolved: 'Answered',
        error: "Didn't send",
    };

    function renderActionFrame(frame) {
        var id = frame.action_id;
        if (!id) return;
        var existing = actionCards[id];
        if (existing) {
            existing.frame = Object.assign({}, existing.frame, frame);
            paintActionCard(existing);
            return;
        }
        var entry = { el: el('article', 'message message--action'), frame: frame };
        actionCards[id] = entry;
        paintActionCard(entry);
        placeInFeed(entry.el, frame.timestamp);
    }

    function paintActionCard(entry) {
        var f = entry.frame;
        var mine = f.player_id === PLAYER_ID || f.mine;
        var status = f.status || 'pending';
        var card = entry.el;
        card.className = 'message message--action' + (mine ? ' message--mine' : '') +
            (f.private ? ' message--action-private' : '');
        card.dataset.actionId = f.action_id;

        var who = mine ? t(f.private ? 'You, to the DM' : 'You') : (f.name || f.player_id);
        var html = '<div class="message__meta"><span class="message__who">' + escapeHtml(who) +
            '</span><time>' + formatTime(f.timestamp) + '</time></div>';
        if (f.linked_to) {
            var target = actionCards[f.linked_to];
            var tname = f.linked_name || (target && target.frame.name) || 'another player';
            html += '<div class="action-link-note">Goes with ' + escapeHtml(possessive(tname)) + ' move</div>';
        }
        html += '<div class="message__text">' + escapeHtml(f.text) + '</div>';
        html += '<div class="action-foot"><span class="status-chip status-chip--' + status + '">' +
            t(STATUS_TEXT[status]) + '</span></div>';
        card.innerHTML = html;

        var foot = card.querySelector('.action-foot');
        if (mine && status === 'error') {
            addMini(foot, 'Try again', 'mini-btn--gold', function () {
                sendAction(f.text, { private: !!f.private, linked_to: f.linked_to, retryOf: f.action_id });
            });
        }
        if (mine && status !== 'sending') {
            addMini(foot, 'Edit and resend', '', function () {
                fillInput(f.text, true);
            });
        }
        if (!mine && !f.private && (status === 'pending' || status === 'processing')) {
            addMini(foot, 'Go with this', 'mini-btn--gold', function () {
                setLink(f.action_id, f.name || f.player_id);
            });
        }
    }

    function addMini(parent, label, extra, onClick) {
        var b = el('button', 'mini-btn ' + (extra || ''), escapeHtml(t(label)));
        b.type = 'button';
        b.addEventListener('click', onClick);
        parent.appendChild(b);
    }

    function possessive(name) {
        return /s$/i.test(name) ? name + "'" : name + "'s";
    }

    function setLink(actionId, name) {
        if (isPrivateMode) togglePrivateMode(false);
        linkTarget = { action_id: actionId, name: name };
        if (dom.linkChip) {
            dom.linkChip.hidden = false;
            dom.linkChip.querySelector('.link-chip__text').textContent = 'Goes with ' + possessive(name) + ' move';
        }
        if (dom.actionInput) dom.actionInput.focus();
    }

    function clearLink() {
        linkTarget = null;
        if (dom.linkChip) dom.linkChip.hidden = true;
    }


    // ===== Sending actions =====

    function sendAction(text, opts) {
        opts = opts || {};
        text = (text || '').trim();
        if (!text || sending) return;
        sending = true;

        var tempId = opts.retryOf || ('local_' + Date.now());
        if (actionCards[tempId]) {
            actionCards[tempId].frame.status = 'sending';
            paintActionCard(actionCards[tempId]);
        } else {
            renderActionFrame({
                action_id: tempId, player_id: PLAYER_ID, mine: true, name: PLAYER_NAME, text: text,
                private: !!opts.private, linked_to: opts.linked_to || null,
                linked_name: linkTarget && linkTarget.name, status: 'sending',
                timestamp: new Date().toISOString(),
            });
        }
        setActionStatus('Sending…', 'queued');
        if (dom.actionSend) dom.actionSend.disabled = true;

        var body = { action: text, private: !!opts.private };
        if (opts.linked_to && !opts.private) body.linked_to = opts.linked_to;
        if (opts.source) body.source = opts.source;

        fetch(API_BASE + '/action', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + TOKEN },
            body: JSON.stringify(body),
        })
            .then(function (resp) { return resp.json().then(function (data) { return { ok: resp.ok, data: data }; }); })
            .then(function (res) {
                var data = res.data || {};
                if (!data.success) throw new Error(data.error || 'The server refused the action');
                // Swap the local card over to the real action id.
                var entry = actionCards[tempId];
                delete actionCards[tempId];
                if (actionCards[data.action_id]) {
                    // The broadcast beat us here; drop the local copy.
                    if (entry && entry.el.parentNode) entry.el.parentNode.removeChild(entry.el);
                } else if (entry) {
                    entry.frame.action_id = data.action_id;
                    entry.frame.status = 'pending';
                    actionCards[data.action_id] = entry;
                    paintActionCard(entry);
                }
                pendingActionId = data.action_id;
                setActionStatus(opts.private ? 'Whisper sent. Waiting for the DM.' : 'Sent. Waiting for the DM.', 'queued');
                if (dom.actionInput && dom.actionInput.value.trim() === text) {
                    dom.actionInput.value = '';
                    autosize();
                }
                if (!opts.retryOf) clearLink();
            })
            .catch(function (err) {
                var entry = actionCards[tempId];
                if (entry) { entry.frame.status = 'error'; paintActionCard(entry); }
                setActionStatus("Didn't send: " + (err && err.message ? err.message : 'no connection') + '. Tap Try again.', 'error');
                console.error('Action submission error:', err);
            })
            .then(function () {
                sending = false;
                if (dom.actionSend) dom.actionSend.disabled = inputGated;
            });
    }

    function submitAction() {
        if (!dom.actionInput || inputGated) return;
        sendAction(dom.actionInput.value, {
            private: isPrivateMode,
            linked_to: linkTarget ? linkTarget.action_id : null,
        });
    }

    function submitVoiceAction(text) {
        if (!text) return;
        sendAction(text, {
            source: 'voice',
            private: isPrivateMode,
            linked_to: linkTarget ? linkTarget.action_id : null,
        });
    }

    var statusClearTimer = null;

    function updateActionStatus(actionId, status, errorMsg) {
        if (status === 'rejected') {
            setActionStatus(errorMsg || 'Not your turn yet.', 'error');
            return;
        }
        var entry = actionId && actionCards[actionId];
        if (entry && entry.frame.status !== status) {
            if (!(entry.frame.status === 'resolved' && status !== 'resolved')) {
                entry.frame.status = status;
                paintActionCard(entry);
            }
        }
        if (actionId && actionId === pendingActionId) {
            if (status === 'processing') {
                setActionStatus('The DM is reading your action.', 'processing');
            } else if (status === 'resolved') {
                setActionStatus('The DM answered.', 'resolved');
                pendingActionId = null;
                if (statusClearTimer) clearTimeout(statusClearTimer);
                statusClearTimer = setTimeout(function () {
                    if (!pendingActionId) setActionStatus('', '');
                }, 4000);
            }
        }
    }

    function setActionStatus(text, cssStatus) {
        if (!dom.actionStatus) return;
        dom.actionStatus.textContent = t(text);
        dom.actionStatus.className = 'action-bar__status' + (cssStatus ? ' action-bar__status--' + cssStatus : '');
    }

    function autosize() {
        var t = dom.actionInput;
        if (!t) return;
        t.style.height = 'auto';
        t.style.height = Math.min(t.scrollHeight + 4, 132) + 'px';
    }

    function fillInput(text, replace) {
        var t = dom.actionInput;
        if (!t || inputGated) return;
        switchTab('game');
        var cur = t.value.trim();
        t.value = (replace || !cur) ? text : cur + ' ' + text;
        autosize();
        t.focus();
        try { t.setSelectionRange(t.value.length, t.value.length); } catch (e) { /* ignore */ }
    }

    function togglePrivateMode(force) {
        isPrivateMode = typeof force === 'boolean' ? force : !isPrivateMode;
        if (isPrivateMode) clearLink();
        if (dom.privateBtn) {
            dom.privateBtn.classList.toggle('action-bar__private-toggle--active', isPrivateMode);
            dom.privateBtn.setAttribute('aria-pressed', isPrivateMode ? 'true' : 'false');
        }
        if (dom.actionBar) dom.actionBar.classList.toggle('action-bar--whisper', isPrivateMode);
        if (dom.actionInput && !inputGated) {
            dom.actionInput.placeholder = t(isPrivateMode ? 'Whisper to the DM…' : 'What do you do?');
        }
    }


    // ===== Quick prompts =====


    // ===== Character data =====

    var SKILL_ABILITY_MAP = {
        'Acrobatics': 'dexterity', 'Animal Handling': 'wisdom', 'Arcana': 'intelligence',
        'Athletics': 'strength', 'Deception': 'charisma', 'History': 'intelligence',
        'Insight': 'wisdom', 'Intimidation': 'charisma', 'Investigation': 'intelligence',
        'Medicine': 'wisdom', 'Nature': 'intelligence', 'Perception': 'wisdom',
        'Performance': 'charisma', 'Persuasion': 'charisma', 'Religion': 'intelligence',
        'Sleight of Hand': 'dexterity', 'Stealth': 'dexterity', 'Survival': 'wisdom',
    };
    var ABILITIES = ['strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma'];
    var ABILITY_SHORT = {
        strength: 'Str', dexterity: 'Dex', constitution: 'Con',
        intelligence: 'Int', wisdom: 'Wis', charisma: 'Cha',
    };
    var ABILITY_NAME = {
        strength: 'Strength', dexterity: 'Dexterity', constitution: 'Constitution',
        intelligence: 'Intelligence', wisdom: 'Wisdom', charisma: 'Charisma',
    };

    function lowerList(list) {
        return (list || []).map(function (s) { return String(s).toLowerCase(); });
    }

    // 'DEX', 'Dex', 'dexterity' -> 'dexterity'
    function normAbility(s) {
        var k = String(s || '').toLowerCase().replace(/^saving throw:\s*/, '').trim();
        for (var i = 0; i < ABILITIES.length; i++) {
            if (ABILITIES[i] === k || ABILITIES[i].slice(0, 3) === k) return ABILITIES[i];
        }
        return k;
    }

    function getAbilityMod(abilities, name) {
        var ab = (abilities || {})[name];
        var score = ab ? (ab.score || 10) : 10;
        return Math.floor((score - 10) / 2);
    }

    function profBonus(data) { return data.proficiency_bonus || 2; }

    function skillBonus(data, skill) {
        var mod = getAbilityMod(data.abilities, SKILL_ABILITY_MAP[skill]);
        var prof = lowerList(data.skill_proficiencies).indexOf(skill.toLowerCase()) !== -1;
        return mod + (prof ? profBonus(data) : 0);
    }

    function totalLevel(data) {
        return (data.classes || []).reduce(function (s, c) { return s + (c.level || 0); }, 0);
    }

    function fetchCharacter() {
        fetch(API_BASE + '/character/' + encodeURIComponent(PLAYER_ID) + '?token=' + encodeURIComponent(TOKEN))
            .then(function (resp) { return resp.ok ? resp.json() : null; })
            .then(function (data) { if (data) applyCharacter(data); })
            .catch(function (err) { console.error('Failed to fetch character:', err); });
    }

    var characterRefreshTimer = null;
    function scheduleCharacterRefresh(delayMs) {
        if (characterRefreshTimer) clearTimeout(characterRefreshTimer);
        characterRefreshTimer = setTimeout(function () {
            characterRefreshTimer = null;
            fetchCharacter();
        }, delayMs);
    }

    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) scheduleCharacterRefresh(200);
    });
    setInterval(function () { if (!document.hidden) fetchCharacter(); }, 60000);

    function applyCharacter(data) {
        if (!data) return;
        cachedCharacterData = data;
        updateVitals(data);
        updateSheet(data);
        updateBag(data);
        if (dom.detail && !dom.detail.hidden && openDetailKey) refreshOpenDetail();
    }


    // ----- Vitals -----

    function updateVitals(data) {
        var parts = [];
        if (data.race) {
            var race = typeof data.race === 'string' ? data.race : (data.race.name || '');
            if (data.race.subrace) race = data.race.subrace + ' ' + race;
            if (race) parts.push(race);
        }
        if (data.classes && data.classes.length) {
            parts.push(data.classes.map(function (c) { return c.name + ' ' + c.level; }).join(' / '));
        }
        if (dom.sub) dom.sub.textContent = parts.join(', ');

        var hp = data.hit_points_current;
        if (hp === undefined || hp === null) hp = data.hp || 0;
        var maxHp = data.hit_points_max || data.max_hp || hp || 1;
        var temp = data.temporary_hit_points || 0;
        var pct = Math.max(0, Math.min(1, hp / maxHp));
        var count = Math.min(maxHp, 20);
        var on = hp <= 0 ? 0 : Math.max(1, Math.ceil(pct * count));

        if (dom.hpBlocks) {
            var html = '';
            for (var i = 0; i < count; i++) html += '<span class="hp__block' + (i < on ? ' hp__block--on' : '') + '"></span>';
            if (temp > 0) html += '<span class="hp__block hp__block--temp" style="flex:' + Math.min(4, Math.ceil(temp / maxHp * count) || 1) + '"></span>';
            dom.hpBlocks.innerHTML = html;
        }
        if (dom.hp) {
            dom.hp.className = 'hp' + (pct >= 1 ? ' hp--full' : '') + (pct <= .5 && pct > .25 ? ' hp--mid' : '') + (pct <= .25 ? ' hp--low' : '');
            dom.hp.setAttribute('aria-valuenow', hp);
            dom.hp.setAttribute('aria-valuemax', maxHp);
        }
        if (dom.hpText) {
            var t = hp + '/' + maxHp + (temp > 0 ? ' +' + temp : '');
            if (hp <= 0 && (data.death_saves_success || data.death_saves_failure)) {
                t = 'Saves ' + (data.death_saves_success || 0) + '✓ ' + (data.death_saves_failure || 0) + '✗';
            }
            dom.hpText.textContent = t;
        }
        if (dom.acValue && data.armor_class !== undefined) dom.acValue.textContent = data.armor_class;
        if (dom.inspPill) dom.inspPill.hidden = !data.inspiration;

        if (dom.vitalsSlots) {
            dom.vitalsSlots.innerHTML = '';
            slotLevels(data).forEach(function (lv) {
                var g = el('div', 'slot-group', '<span class="slot-group__label">Lv ' + lv.level + '</span>' + pips(lv.max, lv.max - lv.used));
                g.title = (lv.max - lv.used) + ' of ' + lv.max + ' level ' + lv.level + ' slots left';
                dom.vitalsSlots.appendChild(g);
            });
        }

        if (dom.conditions) {
            dom.conditions.innerHTML = '';
            (data.conditions || []).forEach(function (c) {
                var name = typeof c === 'string' ? c : (c.name || '');
                var tag = el('button', 'condition-tag', escapeHtml(name));
                tag.type = 'button';
                tag.addEventListener('click', function () {
                    openDetail({ title: name, body: (c && c.description) || 'Ask the DM how this affects you right now.' });
                });
                dom.conditions.appendChild(tag);
            });
        }
    }

    function pips(max, left, extraCls) {
        var html = '';
        for (var i = 0; i < max; i++) {
            html += '<span class="pip' + (extraCls ? ' ' + extraCls : '') + (i < left ? '' : ' pip--spent') + '"></span>';
        }
        return html;
    }

    function slotLevels(data) {
        var slots = data.spell_slots || {};
        return Object.keys(slots).sort(function (a, b) { return a - b; }).map(function (lv) {
            return { level: lv, max: slots[lv] || 0, used: Math.min(slots[lv] || 0, (data.spell_slots_used || {})[lv] || 0) };
        }).filter(function (s) { return s.max > 0 && String(s.level) !== '0'; });
    }


    // ----- Sheet -----

    function updateSheet(data) {
        // Abilities
        if (dom.abilityGrid && data.abilities) {
            dom.abilityGrid.innerHTML = '';
            ABILITIES.forEach(function (a) {
                var score = (data.abilities[a] || {}).score || 10;
                var mod = Math.floor((score - 10) / 2);
                var b = el('div', 'ability-score' + (mod >= 3 ? ' ability-score--high' : ''),
                    '<div class="ability-score__name">' + escapeHtml(t(ABILITY_NAME[a])) + '</div>' +
                    '<div class="ability-score__modifier">' + formatMod(mod) + '</div>' +
                    '<div class="ability-score__value">' + score + '</div>');
                dom.abilityGrid.appendChild(b);
            });
        }

        var dexMod = getAbilityMod(data.abilities, 'dexterity');
        var passive = 10 + skillBonus(data, 'Perception');
        if (dom.sheetFacts) {
            dom.sheetFacts.innerHTML = [
                ['Proficiency', formatMod(profBonus(data))],
                ['Initiative', formatMod(dexMod)],
                ['Speed', (data.speed || 30) + ' ft'],
                ['Passive Perception', passive],
            ].map(function (f) {
                return '<div class="fact"><div class="fact__v">' + escapeHtml(f[1]) + '</div><div class="fact__k">' + escapeHtml(t(f[0])) + '</div></div>';
            }).join('');
        }

        // Saving throws (the engine stores 'DEX' or 'dexterity'; accept both)
        if (dom.savingThrowsList && data.abilities) {
            dom.savingThrowsList.innerHTML = '';
            var saveProfs = (data.saving_throw_proficiencies || []).map(normAbility);
            ABILITIES.forEach(function (a) {
                var prof = saveProfs.indexOf(a) !== -1;
                var bonus = getAbilityMod(data.abilities, a) + (prof ? profBonus(data) : 0);
                var b = el('button', 'save-item' + (prof ? ' save-item--proficient' : ''),
                    '<span class="save-item__dot" aria-hidden="true"></span>' +
                    '<span class="save-item__name">' + escapeHtml(t(ABILITY_SHORT[a])) + '</span>' +
                    '<span class="save-item__bonus">' + formatMod(bonus) + '</span>');
                b.type = 'button';
                b.setAttribute('aria-label', ABILITY_NAME[a] + ' save ' + formatMod(bonus) + (prof ? ', proficient' : ''));
                b.addEventListener('click', function () {
                    openDetail({
                        title: t('{ability} saving throw', { ability: t(ABILITY_NAME[a]) }),
                        meta: [formatMod(bonus), t(prof ? 'Proficient' : 'Not proficient')],
                        body: t('The DM calls for saves. Roll a d20 and add {bonus}.', { bonus: formatMod(bonus) }),
                    });
                });
                dom.savingThrowsList.appendChild(b);
            });
        }

        // Skills
        if (dom.skillsList && data.abilities) {
            dom.skillsList.innerHTML = '';
            var profs = lowerList(data.skill_proficiencies);
            Object.keys(SKILL_ABILITY_MAP).forEach(function (skill) {
                var ability = SKILL_ABILITY_MAP[skill];
                var prof = profs.indexOf(skill.toLowerCase()) !== -1;
                var bonus = skillBonus(data, skill);
                var b = el('button', 'skill-item' + (prof ? ' skill-item--proficient' : ''),
                    '<span class="skill-item__dot" aria-hidden="true"></span>' +
                    '<span class="skill-item__name">' + escapeHtml(t(skill)) + '</span>' +
                    '<span class="skill-item__ability">' + escapeHtml(t(ABILITY_SHORT[ability])) + '</span>' +
                    '<span class="skill-item__bonus">' + formatMod(bonus) + '</span>');
                b.type = 'button';
                b.setAttribute('aria-label', skill + ' ' + formatMod(bonus) + (prof ? ', proficient' : ''));
                b.addEventListener('click', function () {
                    openDetail({
                        title: t(skill),
                        meta: [formatMod(bonus), t(ABILITY_NAME[ability]), t(prof ? 'Proficient' : 'Not proficient')],
                        body: t(SKILL_HINTS[skill] || ''),
                        actions: [{ label: t('Use {name}', { name: t(skill) }), fill: t(skill) + (LANG === 'en' ? ': ' : ':') }],
                    });
                });
                dom.skillsList.appendChild(b);
            });
        }

        updateSpells(data);
        updateFeatures(data);
        updateProficiencies(data);
    }

    var SKILL_HINTS = {
        'Perception': 'Notice things: sounds, movement, something out of place.',
        'Insight': 'Read a person: are they lying, scared, hiding something?',
        'Investigation': 'Search and reason: find clues, work out how something works.',
        'Stealth': 'Move unseen and unheard.',
        'Sleight of Hand': 'Pick a pocket, palm an object, plant something unnoticed.',
        'Persuasion': 'Convince someone honestly, with charm or good reasons.',
        'Deception': 'Mislead someone with a lie, a disguise or a bluff.',
        'Athletics': 'Climb, jump, swim, shove or grapple.',
        'Acrobatics': 'Keep your balance, tumble, slip free.',
        'Performance': 'Entertain a crowd: music, story, dance.',
        'Intimidation': 'Get your way through threats or a hard stare.',
        'Survival': 'Track, find your way, read the weather and the wild.',
        'Arcana': 'Know about magic, spells and magical things.',
        'History': 'Recall people, places and events of the past.',
        'Nature': 'Know plants, animals and the land.',
        'Religion': 'Know gods, rites and holy symbols.',
        'Medicine': 'Steady the dying, judge an illness or wound.',
        'Animal Handling': 'Calm, read or guide an animal.',
    };

    function spellAbility(data) {
        var ability = data.spellcasting_ability;
        if (!ability && data.classes) {
            var byClass = { wizard: 'intelligence', cleric: 'wisdom', druid: 'wisdom', bard: 'charisma',
                sorcerer: 'charisma', warlock: 'charisma', paladin: 'charisma', ranger: 'wisdom', artificer: 'intelligence' };
            data.classes.forEach(function (c) { ability = ability || byClass[String(c.name).toLowerCase()]; });
        }
        return ability ? normAbility(ability) : null;
    }

    function updateSpells(data) {
        var spells = data.spells_known || [];
        var slots = slotLevels(data);
        var hasMagic = spells.length || slots.length;
        if (dom.spellsSection) dom.spellsSection.hidden = !hasMagic;
        if (dom.jumpSpells) dom.jumpSpells.hidden = !hasMagic;
        if (!hasMagic) return;

        var ability = spellAbility(data);
        if (dom.spellcastingHeader) {
            dom.spellcastingHeader.innerHTML = '';
            if (ability) {
                var mod = getAbilityMod(data.abilities, ability);
                [['Ability', ABILITY_NAME[ability] || ability], ['Save DC', 8 + profBonus(data) + mod],
                    ['Attack', formatMod(profBonus(data) + mod)]].forEach(function (s) {
                    dom.spellcastingHeader.appendChild(el('div', 'spellcasting-stat',
                        '<div class="spellcasting-stat__value">' + escapeHtml(s[1]) + '</div>' +
                        '<div class="spellcasting-stat__label">' + s[0] + '</div>'));
                });
            }
        }

        if (dom.spellSlots) {
            dom.spellSlots.innerHTML = '';
            slots.forEach(function (lv) {
                dom.spellSlots.appendChild(el('div', 'spell-slot-level',
                    '<span class="spell-slot-level__label">Level ' + lv.level + ' slots</span>' +
                    '<span class="spell-slot-level__dots">' + pips(lv.max, lv.max - lv.used) + '</span>'));
            });
        }

        if (!dom.spellsKnownList) return;
        dom.spellsKnownList.innerHTML = '';
        var grouped = {};
        spells.forEach(function (sp) {
            var lv = (sp && sp.level !== undefined) ? sp.level : 0;
            (grouped[lv] = grouped[lv] || []).push(sp);
        });
        Object.keys(grouped).sort(function (a, b) { return a - b; }).forEach(function (lv) {
            var group = el('div', 'spell-level-group');
            group.appendChild(el('div', 'spell-level-group__header', String(lv) === '0' ? 'Cantrips (at will)' : 'Level ' + lv));
            grouped[lv].forEach(function (sp) {
                var name = typeof sp === 'string' ? sp : sp.name;
                var tags = '';
                if (sp.concentration) tags += '<span class="row-btn__tag" title="Concentration">C</span>';
                if (sp.ritual) tags += '<span class="row-btn__tag" title="Ritual">R</span>';
                var aside = sp.casting_time ? escapeHtml(sp.casting_time) : '';
                var b = rowButton(name, tags + (aside ? '<span class="row-btn__aside">' + aside + '</span>' : ''));
                b.addEventListener('click', function () { openSpell(sp); });
                group.appendChild(b);
            });
            dom.spellsKnownList.appendChild(group);
        });
    }

    function openSpell(sp) {
        var meta = [];
        meta.push(String(sp.level) === '0' || !sp.level ? 'Cantrip' : 'Level ' + sp.level);
        if (sp.school) meta.push(capitalize(sp.school));
        if (sp.casting_time) meta.push(sp.casting_time);
        if (sp.range !== undefined && sp.range !== null && sp.range !== '') meta.push(typeof sp.range === 'number' ? sp.range + ' ft' : String(sp.range));
        if (sp.duration) meta.push(capitalize(sp.duration));
        if (sp.components && sp.components.length) meta.push(sp.components.join(', ') + (sp.material_components ? ' (' + sp.material_components + ')' : ''));
        if (sp.concentration) meta.push('Concentration');
        if (sp.ritual) meta.push('Ritual');
        openDetail({
            title: sp.name || String(sp),
            meta: meta,
            body: sp.description || '',
            actions: [{ label: 'Cast ' + (sp.name || 'it'), fill: 'I cast ' + (sp.name || '') + ' ' }],
        });
    }

    function capitalize(s) { s = String(s || ''); return s.charAt(0).toUpperCase() + s.slice(1); }

    function rowButton(name, asideHtml, extraCls) {
        var b = el('button', 'row-btn' + (extraCls ? ' ' + extraCls : ''),
            '<span class="row-btn__name">' + escapeHtml(name) + '</span>' + (asideHtml || ''));
        b.type = 'button';
        return b;
    }

    var HEROIC_TEXT = 'You can spend Heroic Inspiration to reroll any one die right after you roll it. ' +
        'You must use the new roll. You have it or you don\'t; it doesn\'t stack.';

    function openInspiration() {
        openDetail({
            title: 'Heroic Inspiration',
            meta: ['Ready to use'],
            body: HEROIC_TEXT,
            actions: [{ label: 'Use it on a reroll', fill: 'I use Heroic Inspiration to reroll that.' }],
        });
    }

    function updateFeatures(data) {
        if (!dom.featuresList) return;
        dom.featuresList.innerHTML = '';

        if (data.inspiration) {
            var li0 = el('li');
            var ib = rowButton('Heroic Inspiration', '<span class="row-btn__aside">Ready</span>', 'row-btn--insp');
            ib.addEventListener('click', openInspiration);
            li0.appendChild(ib);
            dom.featuresList.appendChild(li0);
        }

        var seen = {};
        var features = (data.features || []).filter(function (f) {
            var k = String(f.name).toLowerCase();
            if (seen[k]) return false;
            seen[k] = true;
            return true;
        });
        var legacy = features.length ? [] : (data.features_and_traits || []);

        features.forEach(function (f) {
            var aside = '';
            if (f.uses_max !== null && f.uses_max !== undefined) {
                var left = Math.max(0, f.uses_max - (f.uses_used || 0));
                aside = f.uses_max <= 8 ? '<span class="row-btn__uses">' + pips(f.uses_max, left, 'pip--uses') + '</span>'
                    : '<span class="row-btn__aside">' + left + '/' + f.uses_max + '</span>';
            } else if (f.source) {
                aside = '<span class="row-btn__aside">' + escapeHtml(f.source) + '</span>';
            }
            var li = el('li');
            var b = rowButton(f.name, aside);
            b.addEventListener('click', function () { openFeature(f); });
            li.appendChild(b);
            dom.featuresList.appendChild(li);
        });
        legacy.forEach(function (name) {
            var li = el('li');
            var b = rowButton(name);
            b.addEventListener('click', function () { openDetail({ title: name, body: '' }); });
            li.appendChild(b);
            dom.featuresList.appendChild(li);
        });
        if (!data.inspiration && !features.length && !legacy.length) {
            dom.featuresList.innerHTML = '<li class="muted">No features yet.</li>';
        }
    }

    function openFeature(f) {
        var meta = [];
        if (f.source) meta.push(f.source);
        if (f.level_gained) meta.push('Level ' + f.level_gained);
        if (f.uses_max !== null && f.uses_max !== undefined) {
            meta.push(Math.max(0, f.uses_max - (f.uses_used || 0)) + ' of ' + f.uses_max + ' left');
            if (f.recharge === 'long' && f.short_rest_regain) meta.push(f.short_rest_regain + ' back on a short rest, all on a long rest');
            else if (f.recharge) meta.push('Back on a ' + f.recharge + ' rest');
        }
        openDetail({
            title: f.name,
            meta: meta,
            body: f.description || '',
            actions: f.uses_max ? [{ label: 'Use ' + f.name, fill: 'I use ' + f.name + ' ' }] : [],
        });
    }

    function updateProficiencies(data) {
        if (!dom.proficienciesSummary) return;
        var tools = (data.tool_proficiencies || []).filter(function (t) { return !/^saving throw/i.test(t); });
        var gear = (data.armor_proficiencies || []).concat(data.weapon_proficiencies || []);
        var groups = [
            { label: 'Languages', items: data.languages || [] },
            { label: 'Tools and instruments', items: tools },
            { label: 'Armor and weapons', items: gear },
        ].filter(function (g) { return g.items.length; });
        dom.proficienciesSummary.innerHTML = groups.length ? groups.map(function (g) {
            return '<div class="prof-group"><div class="prof-group__label">' + g.label + '</div>' +
                '<div class="prof-group__items">' + g.items.map(escapeHtml).join(', ') + '</div></div>';
        }).join('') : '<div class="muted">None recorded.</div>';
    }


    // ----- Bag -----

    var COIN_RE = /^(platinum|gold|electrum|silver|copper)( pieces?| coins?)?$/i;
    var COIN_KEY = { platinum: 'pp', gold: 'gp', electrum: 'ep', silver: 'sp', copper: 'cp' };

    function itemName(item) { return typeof item === 'string' ? item : (item && item.name) || ''; }

    function openItem(item, where) {
        if (typeof item === 'string') { openDetail({ title: item, body: '' }); return; }
        var meta = [];
        if (where) meta.push(where);
        var type = item.item_type || item.type;
        if (type && type !== 'misc') meta.push(capitalize(type));
        if (item.quantity > 1) meta.push('×' + item.quantity);
        if (item.weight) meta.push(item.weight + ' lb');
        if (item.value) meta.push(String(item.value));
        if (item.properties) {
            Object.keys(item.properties).forEach(function (k) { meta.push(k + ': ' + item.properties[k]); });
        }
        openDetail({
            title: item.name,
            meta: meta,
            body: item.description || '',
            actions: [{ label: 'Use it', fill: 'I use my ' + item.name + ' ' }],
        });
    }

    function updateBag(data) {
        var inv = data.inventory || [];
        var quest = [], pack = [], coins = {};

        inv.forEach(function (item) {
            var name = itemName(item);
            var type = (item && (item.item_type || item.type)) || '';
            var coin = name.match(COIN_RE);
            if (coin && typeof item === 'object') {
                var key = COIN_KEY[coin[1].toLowerCase()];
                coins[key] = (coins[key] || 0) + (item.quantity || 0);
            } else if (String(type).toLowerCase() === 'quest') {
                quest.push(item);
            } else {
                pack.push(item);
            }
        });

        // Quest items first: what the story hangs on.
        if (dom.questSection && dom.questList) {
            dom.questSection.hidden = !quest.length;
            dom.questList.innerHTML = '';
            quest.forEach(function (item) {
                var li = el('li');
                var b = rowButton(itemName(item), '', 'row-btn--quest');
                b.addEventListener('click', function () { openItem(item, 'Quest item'); });
                li.appendChild(b);
                dom.questList.appendChild(li);
            });
        }

        if (dom.equipmentSlots) {
            dom.equipmentSlots.innerHTML = '';
            var eq = data.equipment || {};
            [['weapon_main', 'Main hand'], ['weapon_off', 'Off hand'], ['armor', 'Armor'], ['shield', 'Shield']]
                .forEach(function (slot) {
                    var item = eq[slot[0]];
                    if (!item && (slot[0] === 'shield' || slot[0] === 'weapon_off')) return;
                    var row = el('div', 'equip-slot', '<span class="equip-slot__label">' + slot[1] + '</span>');
                    var b = rowButton(item ? itemName(item) : 'Nothing',
                        item && item.description ? '<span class="row-btn__aside">' + escapeHtml(shorten(item.description, 26)) + '</span>' : '');
                    if (!item) b.classList.add('equip-slot__item--empty');
                    else b.addEventListener('click', function () { openItem(item, slot[1]); });
                    row.appendChild(b);
                    dom.equipmentSlots.appendChild(row);
                });
        }

        if (dom.inventoryList) {
            dom.inventoryList.innerHTML = '';
            if (!pack.length) dom.inventoryList.innerHTML = '<li class="muted">Your pack is empty.</li>';
            pack.forEach(function (item) {
                var aside = '';
                if (item && item.quantity > 1) aside += '<span class="row-btn__aside">×' + item.quantity + '</span>';
                if (item && item.equipped) aside += '<span class="row-btn__tag">E</span>';
                var li = el('li');
                var b = rowButton(itemName(item), aside);
                b.addEventListener('click', function () { openItem(item); });
                li.appendChild(b);
                dom.inventoryList.appendChild(li);
            });
        }

        // Coin: from the currency field and any coin "items"
        var cur = data.currency || data.gold || null;
        if (typeof cur === 'number') coins.gp = (coins.gp || 0) + cur;
        else if (cur && typeof cur === 'object') {
            Object.keys(COIN_KEY).forEach(function (k) {
                var c = COIN_KEY[k];
                if (cur[c]) coins[c] = (coins[c] || 0) + cur[c];
            });
        }
        if (dom.currencySection && dom.currencyDisplay) {
            var order = [['pp', 'platinum'], ['gp', 'gold'], ['ep', 'electrum'], ['sp', 'silver'], ['cp', 'copper']];
            var html = order.filter(function (o) { return coins[o[0]] > 0; }).map(function (o) {
                return '<div class="currency-item"><span class="currency-item__value">' + coins[o[0]] +
                    '</span><span class="currency-item__label">' + o[1] + '</span></div>';
            }).join('');
            dom.currencySection.hidden = !html;
            dom.currencyDisplay.innerHTML = html;
        }
    }

    function shorten(s, n) {
        s = String(s || '').split(/[;.]/)[0];
        return s.length > n ? s.slice(0, n - 1) + '…' : s;
    }


    // ===== Detail sheet, menu, lightbox =====

    var openDetailKey = null;
    var lastFocus = null;

    function openDetail(opts) {
        if (!dom.detail) return;
        openDetailKey = opts.title;
        dom.detail.querySelector('.detail__title').textContent = opts.title || '';
        dom.detail.querySelector('.detail__meta').innerHTML = (opts.meta || []).filter(Boolean)
            .map(function (m) { return '<span>' + escapeHtml(m) + '</span>'; }).join('');
        var body = dom.detail.querySelector('.detail__body');
        body.innerHTML = opts.body ? paragraphs(opts.body) : '<p class="muted">' + escapeHtml(t('No description recorded. Ask the DM.')) + '</p>';
        if (opts.actions && opts.actions.length) {
            var row = el('div', 'detail__actions');
            opts.actions.forEach(function (a) {
                addMini(row, a.label, '', function () { closeOverlay(dom.detail); fillInput(a.fill); });
            });
            body.appendChild(row);
        }
        showOverlay(dom.detail);
    }

    function refreshOpenDetail() { /* sheet data changed; the open card stays as is until closed */ }

    function showOverlay(node) {
        lastFocus = document.activeElement;
        node.hidden = false;
        var close = node.querySelector('.detail__close');
        if (close) close.focus();
    }

    function closeOverlay(node) {
        if (!node || node.hidden) return;
        node.hidden = true;
        if (node === dom.detail) openDetailKey = null;
        if (lastFocus && lastFocus.focus) { try { lastFocus.focus({ preventScroll: true }); } catch (e) { /* ignore */ } }
    }

    function openLightbox(src, caption) {
        if (!dom.lightbox) return;
        dom.lightbox.querySelector('img').src = src;
        dom.lightbox.querySelector('img').alt = caption || 'A picture from the DM';
        dom.lightbox.querySelector('.lightbox__caption').textContent = caption || '';
        dom.lightbox.hidden = false;
    }


    // ===== DM presence =====

    var dmLastActive = null;

    function setDmPresence(iso) {
        if (iso) dmLastActive = iso;
        renderDmPresence();
    }

    function renderDmPresence() {
        var p = document.getElementById('dm-presence');
        if (!p) return;
        var t = dmLastActive ? Date.parse(dmLastActive) : NaN;
        if (isNaN(t)) { p.hidden = true; return; }
        var mins = Math.max(0, Math.floor((Date.now() - t) / 60000));
        p.classList.remove('dm-presence--here', 'dm-presence--recent', 'dm-presence--away');
        if (mins < 2) {
            // The DM is at the table: nothing to say.
            p.hidden = true;
            return;
        }
        p.hidden = false;
        if (mins < 35) {
            p.textContent = 'DM last active ' + mins + ' min ago';
            p.classList.add('dm-presence--recent');
        } else {
            var d = new Date(t);
            var when = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
            if (d.toDateString() !== new Date().toDateString()) when = d.toLocaleDateString([], { weekday: 'short' }) + ' ' + when;
            p.textContent = 'DM away since ' + when + '. Your actions are saved.';
            p.classList.add('dm-presence--away');
        }
    }
    setInterval(renderDmPresence, 30000);


    // ===== Notifications (tab badge, vibration, Web Push) =====

    var BASE_TITLE = document.title;
    var unreadCount = 0;
    var notifiedIds = {};
    var swRegistration = null;
    var pushSubscribed = false;
    var pageOpenedAt = Date.now();

    function isLooking() { return !document.hidden && document.hasFocus(); }

    function notifyNew(msg, text, isPrivate) {
        if (!((text || '') + '').trim()) return;
        if (msg.id) {
            var key = msg.id + (isPrivate ? ':p' : '');
            if (notifiedIds[key]) return;
            notifiedIds[key] = true;
        }
        if (isLooking() || Date.now() - pageOpenedAt < 5000) return;
        unreadCount++;
        document.title = '(' + unreadCount + ') ' + BASE_TITLE;
        try { if (navigator.vibrate) navigator.vibrate(150); } catch (e) { /* ignore */ }
        // A push-subscribed device gets the notification from the server's
        // Web Push (sw.js); showing one here too would double it.
        if (!pushSubscribed && swRegistration && window.Notification && Notification.permission === 'granted') {
            var body = ((text || '') + '').replace(/[*_#>`]/g, '').replace(/\s+/g, ' ').trim();
            if (body.length > 140) body = body.slice(0, 137) + '…';
            try {
                swRegistration.showNotification(isPrivate ? 'The DM (private)' : 'The DM', {
                    body: body, tag: 'dm-reply', renotify: true,
                });
            } catch (e) { /* ignore */ }
        }
    }

    function clearUnread() {
        if (!isLooking()) return;
        unreadCount = 0;
        document.title = BASE_TITLE;
    }
    document.addEventListener('visibilitychange', clearUnread);
    window.addEventListener('focus', clearUnread);

    function setMenuState(btn, label, state) {
        if (!btn) return;
        btn.innerHTML = '<span>' + escapeHtml(t(label)) + '</span><span class="menu-row__state">' + escapeHtml(t(state)) + '</span>';
    }

    function updateNotifyButton() {
        var on = !!(window.Notification && Notification.permission === 'granted' && swRegistration);
        setMenuState(dom.notifyBtn, 'Notify me when the DM replies', on ? 'On' : 'Off');
    }

    function updateMenuButtons() {
        setMenuState(dom.textSizeBtn, 'Text size', TEXT_SIZES[textSize]);
        if (dom.langBtn) {
            // Always show both names so the row is findable in any language.
            dom.langBtn.innerHTML = '<span>' + escapeHtml(LANG === 'en' ? 'Language' : t('Language') + ' / Language') +
                '</span><span class="menu-row__state">' + escapeHtml(LANGUAGE_NAMES[LANG] || LANG) + '</span>';
        }
        updateNotifyButton();
    }

    function wsSend(obj) {
        if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
    }

    // The server remembers each player's language. A choice made on this device wins;
    // a device that has never chosen follows what the server has (or its own locale).
    function syncLanguageWithServer(serverLang) {
        if (langChosen) {
            if (serverLang !== LANG) wsSend({ type: 'set_language', language: LANG });
        } else if (serverLang && serverLang !== 'en' && serverLang !== LANG) {
            setLanguage(serverLang, false);
        } else if (LANG !== 'en' && serverLang !== LANG) {
            wsSend({ type: 'set_language', language: LANG });
        }
    }

    function setLanguage(code, tellServer) {
        if (LANGS.indexOf(code) === -1) return;
        LANG = code;
        if (tellServer) {
            langChosen = code;
            store('lang', code);
            wsSend({ type: 'set_language', language: code });
        }
        applyStaticText();
        updateMenuButtons();
        if (dom.actionInput) dom.actionInput.placeholder = t(isPrivateMode ? 'Whisper to the DM…' : 'What do you do?');
        if (sttRecognition) sttRecognition.lang = speechLocale();
        if (cachedCharacterData) applyCharacter(cachedCharacterData);
        reloadFeed();
    }

    // Ask for the whole story again so it arrives in the new language.
    function reloadFeed() {
        stopAudio();
        if (dom.feed) dom.feed.querySelectorAll('[data-ts]').forEach(function (n) { n.parentNode.removeChild(n); });
        renderedMsgIds = {};
        actionCards = {};
        lastSeenTimestamp = null;
        if (dom.emptyState) dom.emptyState.hidden = false;
        wsSend({ type: 'history_request', since: '' });
    }

    function speechLocale() {
        return LANG === 'zh' ? 'zh-CN' : (LANG === 'zh-Hant' ? 'zh-TW' : (navigator.language || 'en-US'));
    }

    function urlBase64ToUint8Array(base64String) {
        var padding = '='.repeat((4 - base64String.length % 4) % 4);
        var base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
        var rawData = window.atob(base64);
        var outputArray = new Uint8Array(rawData.length);
        for (var i = 0; i < rawData.length; ++i) outputArray[i] = rawData.charCodeAt(i);
        return outputArray;
    }

    // Lets sw.js reopen this player's /play?token=... link from a notification.
    function rememberPlayUrl() {
        if (!window.caches) return;
        caches.open('dm20-play-url').then(function (c) {
            return c.put('/__play-url', new Response(window.location.pathname + window.location.search));
        }).catch(function () { /* best effort */ });
    }

    function subscribeForPush(reg) {
        if (!reg || !reg.pushManager || !VAPID_PUBLIC_KEY) return;
        reg.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlBase64ToUint8Array(VAPID_PUBLIC_KEY),
        }).then(function (sub) {
            return fetch(API_BASE + '/push/subscribe?token=' + encodeURIComponent(TOKEN), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(sub.toJSON()),
            });
        }).then(function (res) {
            pushSubscribed = !!(res && res.ok);
            if (pushSubscribed) rememberPlayUrl();
        }).catch(function (e) { console.error('Push subscribe failed:', e); });
    }

    function registerNotifyWorker() {
        if (!('serviceWorker' in navigator)) return Promise.resolve(null);
        return navigator.serviceWorker.register('/static/sw.js').then(function (reg) {
            swRegistration = reg;
            updateNotifyButton();
            subscribeForPush(reg);
            return reg;
        }).catch(function () { return null; });
    }

    function initNotifications() {
        if (window.Notification && Notification.permission === 'granted') registerNotifyWorker();
        updateNotifyButton();
        if (!dom.notifyBtn) return;
        dom.notifyBtn.addEventListener('click', function () {
            if (!window.Notification || !('serviceWorker' in navigator)) {
                addSystemMessage('This browser can’t show notifications here. The tab title shows (1), (2)… when the DM replies.');
                return;
            }
            if (Notification.permission === 'denied') {
                addSystemMessage('Notifications are blocked for this site. Allow them in your browser’s site settings.');
                return;
            }
            Notification.requestPermission().then(function (p) {
                if (p === 'granted') {
                    registerNotifyWorker().then(function () {
                        addSystemMessage('Notifications on. You’ll get a ping when the DM replies, even with this tab closed.');
                    });
                }
                updateNotifyButton();
            });
        });
    }


    // ===== Combat =====

    var combatActive = false;
    var simultaneousTimer = null;

    function updateCombatState(data) {
        if (!data || !dom.combatBanner) return;
        if (!data.active) {
            if (combatActive) addSystemMessage('Combat ended');
            combatActive = false;
            dom.combatBanner.classList.remove('combat-banner--active', 'combat-banner--your-turn',
                'combat-banner--waiting', 'combat-banner--simultaneous');
            enableActionInput();
            clearSimultaneousTimer();
            return;
        }
        combatActive = true;
        dom.combatBanner.classList.add('combat-banner--active');
        if (data.mode === 'simultaneous') renderSimultaneousMode(data);
        else renderTurnBasedMode(data);
    }

    function renderTurnBasedMode(data) {
        var isMyTurn = data.your_turn === true;
        dom.combatBanner.classList.remove('combat-banner--simultaneous');
        dom.combatBanner.classList.toggle('combat-banner--your-turn', isMyTurn);
        dom.combatBanner.classList.toggle('combat-banner--waiting', !isMyTurn);
        clearSimultaneousTimer();
        if (dom.combatInfo) {
            var round = data.round ? ', round ' + data.round : '';
            dom.combatInfo.innerHTML = isMyTurn
                ? '<span class="combat-banner__your-turn-text">Your turn</span>' + round
                : 'Combat' + round + '. Waiting for <span class="combat-banner__current">' +
                    escapeHtml(nameFor(data.initiative, data.current_turn)) + '</span>';
        }
        if (isMyTurn) enableActionInput(); else disableActionInput();
        renderInitiativeList(data.initiative, data.current_turn);
    }

    function nameFor(initiative, id) {
        var hit = (initiative || []).filter(function (e) { return e.id === id; })[0];
        return (hit && hit.name) || id || 'someone';
    }

    function renderSimultaneousMode(data) {
        dom.combatBanner.classList.remove('combat-banner--your-turn', 'combat-banner--waiting');
        dom.combatBanner.classList.add('combat-banner--simultaneous');
        var submitted = data.submitted || [];
        var waiting = data.waiting_for || [];
        if (dom.combatInfo) {
            dom.combatInfo.innerHTML =
                '<div class="combat-banner__prompt">' + escapeHtml(data.prompt || 'Everyone acts at once.') + '</div>' +
                '<div class="combat-banner__simul-status">Sent: ' +
                (submitted.length ? submitted.map(escapeHtml).join(', ') : 'nobody yet') +
                '. Waiting on: ' + (waiting.length ? waiting.map(escapeHtml).join(', ') : 'nobody') + '.</div>';
        }
        var mine = submitted.indexOf(PLAYER_ID) !== -1 || submitted.indexOf(PLAYER_NAME) !== -1;
        if (mine) disableActionInput(); else enableActionInput();
        if (data.timeout_seconds && !simultaneousTimer) startSimultaneousTimer(data.timeout_seconds);
        if (dom.initiativeList) dom.initiativeList.innerHTML = '';
    }

    function renderInitiativeList(initiative, currentTurn) {
        if (!dom.initiativeList || !initiative) return;
        dom.initiativeList.innerHTML = '';
        initiative.forEach(function (entry) {
            var id = entry.id || entry.name || entry;
            var name = entry.name || id;
            var row = el('div', 'initiative-entry');
            if (id === currentTurn) row.classList.add('initiative-entry--current');
            if (id === PLAYER_ID || name === PLAYER_NAME) row.classList.add('initiative-entry--self');
            var hp = entry.hp || 0;
            var maxHp = entry.max_hp || 1;
            var pct = Math.max(0, Math.min(100, (hp / maxHp) * 100));
            var color = pct > 50 ? 'var(--moss)' : (pct > 25 ? 'var(--lantern)' : 'var(--ember)');
            row.innerHTML =
                '<div class="initiative-entry__header"><span class="initiative-entry__name">' + escapeHtml(name) +
                '</span><span class="initiative-entry__init">' + (entry.initiative || 0) + '</span></div>' +
                '<div class="initiative-entry__stats"><div class="initiative-entry__hp-bar"><div class="initiative-entry__hp-fill" style="width:' +
                pct + '%;background:' + color + '"></div></div><span>AC ' + (entry.ac || '?') + '</span></div>' +
                ((entry.conditions && entry.conditions.length) ? '<div class="initiative-entry__conditions">' +
                    entry.conditions.map(function (c) { return '<span class="initiative-entry__condition">' + escapeHtml(c) + '</span>'; }).join(' ') + '</div>' : '');
            dom.initiativeList.appendChild(row);
        });
    }

    function enableActionInput() {
        inputGated = false;
        if (dom.actionInput) {
            dom.actionInput.disabled = false;
            dom.actionInput.placeholder = t(isPrivateMode ? 'Whisper to the DM…' : 'What do you do?');
        }
        if (dom.actionSend) dom.actionSend.disabled = sending;
        if (domMic && sttSupported && !sttDenied) domMic.disabled = false;
        if (dom.actionBar) dom.actionBar.classList.remove('action-bar--gated');
        var overlay = $('.turn-gate-overlay');
        if (overlay) overlay.classList.remove('turn-gate-overlay--active');
    }

    function disableActionInput() {
        inputGated = true;
        if (dom.actionInput) {
            dom.actionInput.disabled = true;
            dom.actionInput.placeholder = t('Waiting for your turn…');
        }
        if (dom.actionSend) dom.actionSend.disabled = true;
        if (domMic) domMic.disabled = true;
        if (sttListening && sttRecognition) { sttRecognition.abort(); stopSTT(); }
        if (dom.actionBar) dom.actionBar.classList.add('action-bar--gated');
        var overlay = $('.turn-gate-overlay');
        if (overlay) overlay.classList.add('turn-gate-overlay--active');
    }

    function startSimultaneousTimer(seconds) {
        clearSimultaneousTimer();
        var timerEl = $('.countdown-timer');
        if (!timerEl) return;
        var remaining = seconds;
        timerEl.style.display = 'block';
        timerEl.textContent = formatCountdown(remaining);
        simultaneousTimer = setInterval(function () {
            remaining--;
            if (remaining <= 0) { clearSimultaneousTimer(); timerEl.style.display = 'block'; timerEl.textContent = 'Time’s up'; }
            else timerEl.textContent = formatCountdown(remaining);
        }, 1000);
    }

    function clearSimultaneousTimer() {
        if (simultaneousTimer) { clearInterval(simultaneousTimer); simultaneousTimer = null; }
        var timerEl = $('.countdown-timer');
        if (timerEl) timerEl.style.display = 'none';
    }

    function formatCountdown(seconds) {
        var m = Math.floor(seconds / 60);
        var s = seconds % 60;
        return m + ':' + (s < 10 ? '0' : '') + s;
    }


    // ===== Audio playback =====

    function getAudioContext() {
        if (!audioContext) {
            try { audioContext = new (window.AudioContext || window.webkitAudioContext)(); }
            catch (e) { console.warn('Web Audio API not available:', e); return null; }
        }
        if (audioContext.state === 'suspended') audioContext.resume();
        return audioContext;
    }

    function base64ToArrayBuffer(base64) {
        var binary = atob(base64);
        var bytes = new Uint8Array(binary.length);
        for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
        return bytes.buffer;
    }

    var SPEAKER_SVG = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 6h3l4-3v10l-4-3H2zM11 5h1v1h1v4h-1v1h-1zM13 3h1v1h1v8h-1v1h-1z"/></svg>';

    function speakButton(responseId) {
        var b = el('button', 'speak-btn', SPEAKER_SVG);
        b.type = 'button';
        b.dataset.responseId = responseId;
        b.setAttribute('aria-label', t('Read aloud'));
        b.title = t('Read aloud');
        b.addEventListener('click', function () {
            if (b === playingButton || b.classList.contains('speak-btn--loading')) { stopAudio(); return; }
            getAudioContext();   // unlock audio inside the tap (required on phones)
            stopAudio();
            speakRequests[responseId] = b;
            b.classList.add('speak-btn--loading');
            b.title = t('Loading voice…');
            wsSend({ type: 'speak', response_id: responseId });
        });
        return b;
    }

    function resetSpeakButton(b) {
        if (!b) return;
        b.classList.remove('speak-btn--loading', 'speak-btn--playing');
        b.title = t('Read aloud');
        b.setAttribute('aria-label', t('Read aloud'));
    }

    function stopAudio() {
        audioPlaybackQueue.length = 0;
        if (currentAudioSource) {
            try { currentAudioSource.onended = null; currentAudioSource.stop(); } catch (e) { /* already stopped */ }
            currentAudioSource = null;
        }
        isPlayingAudio = false;
        resetSpeakButton(playingButton);
        playingButton = null;
        Object.keys(speakRequests).forEach(function (id) { resetSpeakButton(speakRequests[id]); });
        speakRequests = {};
    }

    function audioFailed(responseId) {
        var b = speakRequests[responseId];
        if (b) {
            delete speakRequests[responseId];
            resetSpeakButton(b);
            setActionStatus('Read-aloud is not available right now.', 'error');
        }
    }

    function handleAudioChunk(msg) {
        // Speech only plays when the player tapped the speaker on that message.
        var owner = msg.response_id && speakRequests[msg.response_id];
        if (!owner) return;
        delete speakRequests[msg.response_id];
        if (!msg.total_chunks && !msg.sequence) {
            try { queueAudioPlayback(base64ToArrayBuffer(msg.data), owner); }
            catch (e) { console.warn('Audio decode failed:', e); resetSpeakButton(owner); }
            return;
        }
        var seq = msg.sequence || 0;
        var total = msg.total_chunks || 1;
        var streamId = 'stream_' + total + '_' + (msg.duration_ms || 0);
        if (!audioChunkBuffers[streamId]) {
            audioChunkBuffers[streamId] = { chunks: new Array(total), received: 0, total: total };
        }
        var stream = audioChunkBuffers[streamId];
        stream.chunks[seq] = base64ToArrayBuffer(msg.data);
        stream.received++;
        if (stream.received >= stream.total) {
            var size = 0;
            stream.chunks.forEach(function (b) { if (b) size += b.byteLength; });
            var combined = new Uint8Array(size);
            var offset = 0;
            stream.chunks.forEach(function (b) {
                if (b) { combined.set(new Uint8Array(b), offset); offset += b.byteLength; }
            });
            delete audioChunkBuffers[streamId];
            queueAudioPlayback(combined.buffer, owner);
        }
    }

    function queueAudioPlayback(arrayBuffer, button) {
        audioPlaybackQueue.push({ data: arrayBuffer, button: button || null });
        if (!isPlayingAudio) playNextAudio();
    }

    function playNextAudio() {
        resetSpeakButton(playingButton);
        playingButton = null;
        currentAudioSource = null;
        if (!audioPlaybackQueue.length) { isPlayingAudio = false; return; }
        isPlayingAudio = true;
        var ctx = getAudioContext();
        if (!ctx) { audioPlaybackQueue.length = 0; isPlayingAudio = false; return; }
        var item = audioPlaybackQueue.shift();
        ctx.decodeAudioData(item.data, function (audioBuffer) {
            var source = ctx.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(ctx.destination);
            source.onended = playNextAudio;
            currentAudioSource = source;
            playingButton = item.button;
            if (playingButton) {
                playingButton.classList.remove('speak-btn--loading');
                playingButton.classList.add('speak-btn--playing');
                playingButton.title = t('Stop reading');
                playingButton.setAttribute('aria-label', t('Stop reading'));
            }
            source.start(0);
        }, function (err) {
            console.warn('Failed to decode audio:', err);
            resetSpeakButton(item.button);
            playNextAudio();
        });
    }


    // ===== Speech-to-text (hold the mic to talk) =====

    var sttSupported = false;
    var sttRecognition = null;
    var sttListening = false;
    var sttDenied = false;
    var domMic = null;
    var domMicDot = null;
    var domTranscriptPreview = null;
    var finalTranscript = '';

    function initSTT() {
        var SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) return;  // text input still works

        sttSupported = true;
        sttRecognition = new SpeechRecognition();
        sttRecognition.continuous = true;
        sttRecognition.interimResults = true;
        sttRecognition.maxAlternatives = 1;
        sttRecognition.lang = speechLocale();

        domMic = $('.action-bar__mic');
        domMicDot = $('.mic-listening-dot');
        domTranscriptPreview = $('.voice-transcript-preview');

        if (domMic) {
            domMic.hidden = false;
            domMic.addEventListener('mousedown', function (e) { e.preventDefault(); startSTT(); });
            domMic.addEventListener('mouseup', function () { if (sttListening) sttRecognition.stop(); });
            domMic.addEventListener('mouseleave', function () { if (sttListening) sttRecognition.stop(); });
            domMic.addEventListener('touchstart', function (e) { e.preventDefault(); startSTT(); });
            domMic.addEventListener('touchend', function (e) { e.preventDefault(); if (sttListening) sttRecognition.stop(); });
            domMic.addEventListener('contextmenu', function (e) { e.preventDefault(); });
        }

        sttRecognition.onresult = function (event) {
            var interim = '';
            for (var i = event.resultIndex; i < event.results.length; i++) {
                var t = event.results[i][0].transcript;
                if (event.results[i].isFinal) finalTranscript += t + ' ';
                else interim = t;
            }
            showTranscriptPreview((finalTranscript + interim).trim() || 'Listening…');
        };

        sttRecognition.onerror = function (event) {
            if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
                sttDenied = true;
                if (domMic) {
                    domMic.classList.add('action-bar__mic--denied');
                    domMic.title = 'Microphone access denied';
                }
            } else if (event.error !== 'no-speech') {
                console.warn('STT error:', event.error);
            }
            stopSTT();
        };

        sttRecognition.onend = function () {
            var toSend = finalTranscript.trim();
            finalTranscript = '';
            if (toSend) { hideTranscriptPreview(); submitVoiceAction(toSend); }
            if (sttListening) stopSTT();
        };
    }

    function toggleSTT() {
        if (sttDenied || !sttSupported) return;
        if (sttListening) { sttRecognition.abort(); stopSTT(); }
        else startSTT();
    }

    function startSTT() {
        if (!sttRecognition || sttListening || sttDenied || inputGated) return;
        getAudioContext();
        finalTranscript = '';
        try {
            sttRecognition.start();
            sttListening = true;
            if (domMic) domMic.classList.add('action-bar__mic--listening');
            if (domMicDot) domMicDot.hidden = false;
            showTranscriptPreview('Listening…');
        } catch (e) {
            console.warn('Failed to start STT:', e);
            stopSTT();
        }
    }

    function stopSTT() {
        sttListening = false;
        if (domMic) domMic.classList.remove('action-bar__mic--listening');
        if (domMicDot) domMicDot.hidden = true;
        hideTranscriptPreview();
    }

    function showTranscriptPreview(text) {
        if (!domTranscriptPreview) return;
        domTranscriptPreview.textContent = text;
        domTranscriptPreview.hidden = false;
        domTranscriptPreview.classList.toggle('voice-transcript-preview--active', text !== 'Listening…');
    }

    function hideTranscriptPreview() {
        if (!domTranscriptPreview) return;
        domTranscriptPreview.hidden = true;
        domTranscriptPreview.textContent = '';
        domTranscriptPreview.classList.remove('voice-transcript-preview--active');
    }


    // ===== Wiring =====

    function initEventListeners() {
        if (dom.actionForm) {
            dom.actionForm.addEventListener('submit', function (e) { e.preventDefault(); submitAction(); });
        }
        if (dom.actionInput) {
            dom.actionInput.addEventListener('input', autosize);
            dom.actionInput.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
                    e.preventDefault();
                    submitAction();
                }
            });
        }
        $$('.tab-bar__btn').forEach(function (btn) {
            btn.addEventListener('click', function () { switchTab(btn.dataset.tab); });
        });
        if (dom.privateBtn) dom.privateBtn.addEventListener('click', function () {
            togglePrivateMode();
            if (dom.actionInput) dom.actionInput.focus();
        });
        if (dom.linkChip) dom.linkChip.querySelector('.link-chip__clear').addEventListener('click', clearLink);
        if (dom.inspPill) dom.inspPill.addEventListener('click', openInspiration);

        // Jump links scroll inside the sheet, not the page
        $$('.jump-row a').forEach(function (a) {
            a.addEventListener('click', function (e) {
                e.preventDefault();
                var target = document.querySelector(a.getAttribute('href'));
                if (target) target.scrollIntoView({ block: 'start', behavior: 'smooth' });
            });
        });

        var menuBtn = $('#menu-btn');
        if (menuBtn) menuBtn.addEventListener('click', function () { showOverlay(dom.menu); });

        [dom.detail, dom.menu].forEach(function (ov) {
            if (!ov) return;
            ov.addEventListener('click', function (e) {
                if (e.target.closest('[data-close]')) closeOverlay(ov);
            });
        });
        if (dom.lightbox) dom.lightbox.addEventListener('click', function () { dom.lightbox.hidden = true; });
        document.addEventListener('keydown', function (e) {
            if (e.key !== 'Escape') return;
            if (dom.lightbox && !dom.lightbox.hidden) dom.lightbox.hidden = true;
            else if (dom.detail && !dom.detail.hidden) closeOverlay(dom.detail);
            else if (dom.menu && !dom.menu.hidden) closeOverlay(dom.menu);
        });

        if (dom.langBtn) dom.langBtn.addEventListener('click', function () {
            setLanguage(LANGS[(LANGS.indexOf(LANG) + 1) % LANGS.length], true);
        });
        if (dom.textSizeBtn) dom.textSizeBtn.addEventListener('click', function () {
            textSize = (textSize + 1) % TEXT_SIZES.length;
            store('textSize', TEXT_SIZES[textSize]);
            applyTextSize();
            updateMenuButtons();
        });
    }

    function init() {
        applyTextSize();
        applyStaticText();
        initEventListeners();
        updateMenuButtons();
        initNotifications();
        initSTT();
        fetchCharacter();  // don't wait on the socket for the sheet
        connect();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
