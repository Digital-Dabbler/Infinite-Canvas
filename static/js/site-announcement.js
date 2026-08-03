(function () {
    'use strict';

    const STORAGE_PREFIX = 'infinite_canvas_announcement_shown_at_';
    const REPEAT_MS = 60 * 60 * 1000;
    let currentAnnouncement = null;
    let announcementRequest = null;

    function storageKey(announcement) {
        return STORAGE_PREFIX + String(announcement?.id || 'unknown');
    }

    function wasRecentlyShown(announcement) {
        try {
            const shownAt = Number(localStorage.getItem(storageKey(announcement)));
            const elapsed = Date.now() - shownAt;
            return shownAt > 0 && elapsed >= 0 && elapsed < REPEAT_MS;
        } catch (error) {
            return false;
        }
    }

    function rememberShown(announcement) {
        try {
            localStorage.setItem(storageKey(announcement), String(Date.now()));
        } catch (error) {
            // Storage may be disabled; the announcement should still be usable.
        }
    }

    async function fetchAnnouncement(forceReload) {
        if (currentAnnouncement && !forceReload) return currentAnnouncement;
        if (announcementRequest) return announcementRequest;
        announcementRequest = fetch('/api/announcement', { cache: 'no-store' })
            .then(async (response) => {
                if (!response.ok) return null;
                const data = await response.json();
                currentAnnouncement = data.announcement || null;
                return currentAnnouncement;
            })
            .catch(() => null)
            .finally(() => {
                announcementRequest = null;
            });
        return announcementRequest;
    }

    function appendLinkedText(container, text) {
        container.replaceChildren();
        String(text || '').split(/(https?:\/\/[^\s]+)/g).forEach((part) => {
            if (!/^https?:\/\//i.test(part)) {
                container.appendChild(document.createTextNode(part));
                return;
            }
            const link = document.createElement('a');
            link.className = 'site-announcement__link';
            link.href = part;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.textContent = part;
            container.appendChild(link);
        });
    }

    function formatPublishTime(value) {
        const date = new Date(Number(value) || 0);
        if (!Number.isFinite(date.getTime())) return '';
        return new Intl.DateTimeFormat('zh-CN', {
            year: 'numeric',
            month: 'long',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        }).format(date);
    }

    function createAnnouncementDialog() {
        const existing = document.getElementById('site-announcement');
        if (existing) return existing;

        const style = document.createElement('style');
        style.textContent = `
            #site-announcement {
                width: min(700px, calc(100vw - 28px));
                max-height: calc(100vh - 28px);
                padding: 0;
                border: 1px solid rgba(88, 166, 255, .38);
                border-radius: 20px;
                background: #111722;
                color: #e6edf3;
                box-shadow: 0 30px 100px rgba(0, 0, 0, .72);
                overflow: auto;
                font: 14px/1.65 system-ui, "Microsoft YaHei", sans-serif;
                z-index: 100000;
            }
            #site-announcement::backdrop {
                background: rgba(4, 8, 14, .82);
                backdrop-filter: blur(6px);
            }
            #site-announcement[open] {
                position: fixed;
                inset: 0;
                margin: auto;
            }
            .site-announcement__content { padding: 30px; }
            .site-announcement__badge {
                display: inline-flex;
                padding: 5px 10px;
                border: 1px solid rgba(242, 204, 96, .42);
                border-radius: 999px;
                background: rgba(242, 204, 96, .1);
                color: #f2cc60;
                font-size: 12px;
                font-weight: 700;
            }
            .site-announcement__title {
                margin: 14px 0 6px;
                color: #fff;
                font-size: 25px;
                line-height: 1.35;
            }
            .site-announcement__dates { margin: 0 0 18px; color: #aab7c8; }
            .site-announcement__callout {
                margin: 16px 0;
                padding: 14px 16px;
                border-left: 3px solid #f2cc60;
                border-radius: 8px;
                background: rgba(242, 204, 96, .07);
                color: #e6edf3;
                white-space: pre-wrap;
                overflow-wrap: anywhere;
            }
            .site-announcement__list {
                margin: 16px 0;
                padding-left: 22px;
            }
            .site-announcement__list li + li { margin-top: 9px; }
            .site-announcement__shared {
                padding: 13px 15px;
                border: 1px solid rgba(46, 160, 67, .42);
                border-radius: 10px;
                background: rgba(46, 160, 67, .08);
                color: #aff5b4;
                white-space: pre-wrap;
                overflow-wrap: anywhere;
            }
            .site-announcement__callout[hidden],
            .site-announcement__list[hidden],
            .site-announcement__shared[hidden] { display: none; }
            .site-announcement__link { color: #79c0ff; }
            .site-announcement__button {
                width: 100%;
                margin-top: 22px;
                padding: 12px 16px;
                border: 0;
                border-radius: 9px;
                background: #58a6ff;
                color: #07111f;
                font: inherit;
                font-weight: 700;
                cursor: pointer;
            }
            .site-announcement__button:focus-visible,
            .site-announcement__link:focus-visible {
                outline: 2px solid #fff;
                outline-offset: 3px;
            }
            @media (max-width: 560px) {
                .site-announcement__content { padding: 22px 18px; }
                .site-announcement__title { font-size: 21px; }
            }
        `;

        const dialog = document.createElement('dialog');
        dialog.id = 'site-announcement';
        dialog.setAttribute('aria-labelledby', 'site-announcement-title');
        dialog.innerHTML = `
            <div class="site-announcement__content">
                <span class="site-announcement__badge">重要公告 · 请全员阅读</span>
                <h2 class="site-announcement__title" id="site-announcement-title"></h2>
                <p class="site-announcement__dates"></p>
                <div class="site-announcement__callout"></div>
                <ul class="site-announcement__list"></ul>
                <div class="site-announcement__shared"></div>
                <button class="site-announcement__button" type="button">我已知晓，进入无限画布</button>
            </div>
        `;
        dialog.addEventListener('cancel', (event) => event.preventDefault());
        dialog.querySelector('.site-announcement__button').addEventListener('click', () => {
            if (typeof dialog.close === 'function') dialog.close();
            else dialog.removeAttribute('open');
        });
        document.head.appendChild(style);
        document.body.appendChild(dialog);
        return dialog;
    }

    function renderAnnouncement(dialog, announcement) {
        dialog.querySelector('.site-announcement__title').textContent = announcement.title;
        dialog.querySelector('.site-announcement__dates').textContent =
            `发布时间：${formatPublishTime(announcement.starts_at)}　有效至：${formatPublishTime(announcement.ends_at)}`;
        const blocks = String(announcement.content || '')
            .split(/\n\s*\n/)
            .map((block) => block.trim())
            .filter(Boolean);
        const callout = dialog.querySelector('.site-announcement__callout');
        const list = dialog.querySelector('.site-announcement__list');
        const shared = dialog.querySelector('.site-announcement__shared');
        const summary = blocks.shift() || '';
        const note = blocks.length ? blocks.pop() : '';
        const items = blocks.flatMap((block) => block.split(/\n+/).map((line) => line.trim()).filter(Boolean));

        callout.hidden = !summary;
        appendLinkedText(callout, summary);
        list.replaceChildren();
        items.forEach((item) => {
            const row = document.createElement('li');
            appendLinkedText(row, item.replace(/^[-*•]\s*/, ''));
            list.appendChild(row);
        });
        list.hidden = !items.length;
        shared.hidden = !note;
        appendLinkedText(shared, note);
    }

    async function openAnnouncement(force) {
        const announcement = await fetchAnnouncement(Boolean(force));
        if (!announcement) {
            if (force) window.alert('当前没有正在发布的公告。');
            return;
        }
        if (!force && wasRecentlyShown(announcement)) return;
        const dialog = createAnnouncementDialog();
        renderAnnouncement(dialog, announcement);
        if (dialog.open) return;
        if (typeof dialog.showModal === 'function') dialog.showModal();
        else dialog.setAttribute('open', '');
        rememberShown(announcement);
    }

    window.openSiteAnnouncement = function () {
        return openAnnouncement(true);
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => openAnnouncement(false), { once: true });
    } else {
        openAnnouncement(false);
    }
})();
