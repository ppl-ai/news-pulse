(function() {
  'use strict';

  // ========== CONSTANTS ==========
  const RSS2JSON_API = 'https://api.rss2json.com/v1/api.json?rss_url=';
  const STORAGE_KEY = 'newspulse';

  let FEED_CONFIGS = {
    nyt: {
      name: 'NYT Homepage',
      urls: ['https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml'],
      enabled: true
    },
    wsj: {
      name: 'WSJ (Consolidated)',
      urls: [
        'https://feeds.a.dj.com/rss/RSSWorldNews.xml',
        'https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml',
        'https://feeds.a.dj.com/rss/RSSMarketsMain.xml'
      ],
      subLabels: ['home', 'business', 'markets'],
      enabled: true
    },
    wapo: {
      name: 'Washington Post',
      urls: ['https://news.google.com/rss/search?q=site:washingtonpost.com&hl=en-US&gl=US&ceid=US:en'],
      enabled: true
    }
  };

  let state = {
    discoverStories: [],
    discoverFilter: 'all',
    feedData: {},
    highlightGaps: false,
    gapResults: [],
    customFeeds: [],
    discoverVisible: 10,
    feedVisible: {},
    wsjFilter: 'all',
    wsjAllItems: [],
    gapVisible: 10,
    pasteTopic: 'top',
    currentOverlay: null,
    userAddedStories: [],
  };

  Object.keys(FEED_CONFIGS).forEach(k => { state.feedVisible[k] = 10; });

  // ========== LOCAL STORAGE ==========
  function loadSaved() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
      if (saved.customFeeds) state.customFeeds = saved.customFeeds;
      if (saved.userAddedStories) state.userAddedStories = saved.userAddedStories;
      if (saved.disabledFeeds) {
        saved.disabledFeeds.forEach(id => {
          if (FEED_CONFIGS[id]) FEED_CONFIGS[id].enabled = false;
        });
      }
    } catch (e) { /* ignore */ }
  }

  function savePreferences() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        customFeeds: state.customFeeds,
        userAddedStories: state.userAddedStories,
        disabledFeeds: Object.entries(FEED_CONFIGS)
          .filter(([, cfg]) => !cfg.enabled)
          .map(([id]) => id),
      }));
    } catch (e) { /* ignore */ }
  }

  // ========== UTILITIES ==========
  function timeAgo(dateStr) {
    if (!dateStr) return '';
    try {
      const d = new Date(dateStr);
      if (isNaN(d.getTime())) return dateStr;
      const now = new Date();
      const diff = Math.floor((now - d) / 1000);
      if (diff < 60) return 'just now';
      if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
      if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
      return Math.floor(diff / 86400) + 'd ago';
    } catch { return ''; }
  }

  function escapeHtml(str) {
    if (!str) return '';
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
  }

  function truncate(str, n) {
    if (!str) return '';
    return str.length > n ? str.slice(0, n) + '\u2026' : str;
  }

  // ========== KEYWORD EXTRACTION ==========
  const STOP_WORDS = new Set([
    'the','a','an','and','or','but','in','on','at','to','for','of','with','by',
    'from','is','it','its','this','that','was','are','were','be','been','being',
    'have','has','had','do','does','did','will','would','could','should','may',
    'might','can','shall','not','no','nor','so','up','out','if','about','who',
    'which','when','where','what','how','all','each','every','both','few','more',
    'most','other','some','such','than','too','very','just','also','now','new',
    'says','said','say','as','after','before','over','between','under','into',
    'during','since','he','she','they','them','their','his','her','we','you',
    'your','our','my','i','me','him','us','there','here','then','why','per','via'
  ]);

  function extractKeywords(title) {
    if (!title) return [];
    return title.toLowerCase()
      .replace(/[^a-z0-9\s'-]/g, '')
      .split(/\s+/)
      .filter(w => w.length > 2 && !STOP_WORDS.has(w));
  }

  // ========== OP-ED DETECTION ==========
  const OPED_PATTERNS = ['opinion', 'editorial', 'commentary', 'review', 'letters to the editor'];

  function isOpEd(title) {
    const lower = (title || '').toLowerCase();
    return OPED_PATTERNS.some(p => lower.includes(p));
  }

  // ========== PUBLISHER NORMALIZATION ==========
  function normalizePublisher(source) {
    const s = (source || '').toLowerCase();
    if (s.includes('nyt') || s.includes('new york times')) return 'NYT';
    if (s.includes('wsj') || s.includes('wall street')) return 'WSJ';
    if (s.includes('wash') || s.includes('wapo')) return 'WaPo';
    return source;
  }

  // ========== GAP ANALYSIS ==========
  function computeGaps() {
    const perplexityKWs = state.discoverStories.map(s => ({
      keywords: extractKeywords(s.title),
      title: s.title
    }));

    const allCompetitor = [];
    for (const [feedId, items] of Object.entries(state.feedData)) {
      (items || []).forEach(item => {
        if (!isOpEd(item.title)) {
          allCompetitor.push({
            ...item,
            feedId,
            publisher: normalizePublisher(item.source || FEED_CONFIGS[feedId]?.name || feedId)
          });
        }
      });
    }

    const gaps = [];
    allCompetitor.forEach(story => {
      const storyKWs = extractKeywords(story.title);
      if (storyKWs.length === 0) return;

      let isCovered = false;
      for (const pStory of perplexityKWs) {
        if (pStory.keywords.length === 0) continue;
        const matching = storyKWs.filter(kw => pStory.keywords.includes(kw));
        if (matching.length / storyKWs.length >= 0.4) {
          isCovered = true;
          break;
        }
      }

      if (!isCovered) gaps.push(story);
    });

    const grouped = {};
    gaps.forEach(g => {
      const key = extractKeywords(g.title).sort().join('|');
      if (!grouped[key]) {
        grouped[key] = { ...g, publishers: new Set(), allStories: [] };
      }
      grouped[key].publishers.add(g.publisher);
      grouped[key].allStories.push(g);
    });

    const ranked = Object.values(grouped).map(g => ({
      title: g.title,
      link: g.link,
      description: g.description,
      publishers: [...g.publishers],
      buzzScore: g.publishers.size,
      relevanceScore: extractKeywords(g.title).reduce((acc, kw) => {
        return acc + gaps.filter(s => extractKeywords(s.title).includes(kw)).length;
      }, 0)
    }));

    ranked.sort((a, b) => {
      if (b.buzzScore !== a.buzzScore) return b.buzzScore - a.buzzScore;
      return b.relevanceScore - a.relevanceScore;
    });

    state.gapResults = ranked;
    return ranked;
  }

  function isStoryAGap(title) {
    if (!state.highlightGaps) return false;
    const storyKWs = extractKeywords(title);
    if (storyKWs.length === 0) return false;

    const perplexityKWs = state.discoverStories.map(s => extractKeywords(s.title));

    for (const pKWs of perplexityKWs) {
      if (pKWs.length === 0) continue;
      const matching = storyKWs.filter(kw => pKWs.includes(kw));
      if (matching.length / storyKWs.length >= 0.4) return false;
    }
    return true;
  }

  // ========== RENDERING ==========
  function renderStoryCard(story, opts = {}) {
    const gap = opts.checkGap && !isOpEd(story.title) && isStoryAGap(story.title);
    const topicClass = opts.topicClass || '';
    const gapClass = gap ? ' has-gap' : '';
    const href = story.link || story.url || '';

    return `
      <div class="story-card${topicClass ? ' ' + topicClass : ''}${gapClass}">
        <div class="gap-dot"></div>
        <div class="card-title">
          <a href="${escapeHtml(href)}" target="_blank" rel="noopener">${escapeHtml(story.title)}</a>
        </div>
        <div class="card-meta">
          <span class="meta-src">${escapeHtml(story.source || '')}</span>
          ${story.topic ? `<span class="topic-tag tag-${story.topic}">${story.topic === 'tech' ? 'Tech & Science' : story.topic === 'finance' ? 'Finance' : 'Top'}</span>` : ''}
          <span>${timeAgo(story.pubDate)}</span>
        </div>
        ${story.description ? `<div class="card-desc">${escapeHtml(truncate(story.description, 200))}</div>` : ''}
      </div>
    `;
  }

  function renderLoadMore(currentCount, totalCount, onClickFn) {
    const remaining = totalCount - currentCount;
    if (remaining <= 0) return '';
    return `<button class="load-more-btn" onclick="${onClickFn}">Show 5 more (${remaining} remaining)</button>`;
  }

  function renderDiscoverColumn() {
    const body = document.getElementById('discover-body');
    let stories = state.discoverStories;

    if (state.discoverFilter !== 'all') {
      stories = stories.filter(s => s.topic === state.discoverFilter);
    }

    document.getElementById('discover-count').textContent = stories.length;

    if (stories.length === 0) {
      body.innerHTML = `<div class="column-status">No Discover stories loaded.<br><br><span style="font-size:10px;color:var(--text-muted)">Use <span class="guide-kbd">P</span> to paste stories manually.</span></div>`;
      return;
    }

    const visible = stories.slice(0, state.discoverVisible);
    let html = visible.map(s => {
      const topicClass = s.topic === 'top' ? 'topic-top' : s.topic === 'tech' ? 'topic-tech' : s.topic === 'finance' ? 'topic-finance' : '';
      return renderStoryCard(s, { topicClass });
    }).join('');

    html += renderLoadMore(visible.length, stories.length, 'showMoreDiscover()');
    body.innerHTML = html;
  }

  function renderFeedColumn(feedId) {
    const body = document.getElementById(feedId + '-body');
    const items = state.feedData[feedId] || [];
    const countEl = document.getElementById(feedId + '-count');
    if (countEl) countEl.textContent = items.length;

    if (items.length === 0) {
      body.innerHTML = `<div class="column-status">No stories loaded.</div>`;
      return;
    }

    const count = state.feedVisible[feedId] || 10;
    const visible = items.slice(0, count);

    let html = visible.map(s => renderStoryCard(s, { checkGap: true })).join('');
    html += renderLoadMore(visible.length, items.length, `showMoreFeed('${feedId}')`);
    body.innerHTML = html;
  }

  function renderAllFeeds() {
    Object.keys(FEED_CONFIGS).forEach(id => {
      if (FEED_CONFIGS[id].enabled) renderFeedColumn(id);
    });
    state.customFeeds.forEach(cf => {
      if (cf.enabled) renderFeedColumn(cf.id);
    });
  }

  function renderGapAnalysis() {
    const body = document.getElementById('gap-analysis-body');
    const gaps = computeGaps();

    if (gaps.length === 0) {
      body.innerHTML = `<div class="column-status" style="padding:40px;color:var(--text-secondary);">No coverage gaps detected. Perplexity Discover appears to have good coverage of current stories.</div>`;
      return;
    }

    const visible = gaps.slice(0, state.gapVisible);
    let html = visible.map((gap, i) => `
      <div class="gap-item">
        <div class="gap-rank">${i + 1}.</div>
        <div class="gap-content">
          <div class="gap-title"><a href="${escapeHtml(gap.link)}" target="_blank" rel="noopener">${escapeHtml(gap.title)}</a></div>
          <div class="gap-sources">Covered by: ${gap.publishers.join(', ')}</div>
          ${gap.description ? `<div class="gap-desc">${escapeHtml(truncate(gap.description, 180))}</div>` : ''}
        </div>
        <div class="gap-buzz">${gap.buzzScore} src${gap.buzzScore > 1 ? 's' : ''}</div>
      </div>
    `).join('');

    const remaining = gaps.length - visible.length;
    if (remaining > 0) {
      html += `<button class="load-more-btn" onclick="showMoreGaps()" style="margin-top:12px">Show 5 more (${remaining} remaining)</button>`;
    }

    body.innerHTML = html;
  }

  function renderSourceManager() {
    const body = document.getElementById('sources-body');
    let html = '';

    Object.entries(FEED_CONFIGS).forEach(([id, cfg]) => {
      html += `
        <div class="source-row">
          <div class="source-info">
            <div class="source-name">${escapeHtml(cfg.name)}</div>
            <div class="source-url">${escapeHtml(cfg.urls[0])}${cfg.urls.length > 1 ? ` (+${cfg.urls.length - 1} more)` : ''}</div>
          </div>
          <button class="source-toggle${cfg.enabled ? ' on' : ''}" onclick="toggleSource('${id}')"></button>
        </div>
      `;
    });

    state.customFeeds.forEach(cf => {
      html += `
        <div class="source-row">
          <div class="source-info">
            <div class="source-name">${escapeHtml(cf.name)} <span style="font-family:var(--font-mono);font-size:9px;color:var(--text-muted)">[CUSTOM]</span></div>
            <div class="source-url">${escapeHtml(cf.url)}</div>
          </div>
          <button class="source-toggle${cf.enabled ? ' on' : ''}" onclick="toggleCustomSource('${cf.id}')"></button>
        </div>
      `;
    });

    html += `
      <div class="add-custom-feed">
        <input type="text" id="custom-feed-url" placeholder="https://example.com/rss.xml">
        <button onclick="addCustomFeed()">ADD FEED</button>
      </div>
    `;

    body.innerHTML = html;
  }

  // ========== DATA FETCHING ==========
  async function fetchDiscover() {
    try {
      const res = await fetch('perplexity_cache.json');
      if (res.ok) {
        const data = await res.json();
        let stories = [];
        if (data.stories && data.stories.length > 0) {
          stories = data.stories.map(s => ({
            ...s,
            link: s.url || s.link || '',
            topic: s.topic || 'top'
          }));
        } else if (data.topics) {
          ['top', 'tech', 'finance'].forEach(t => {
            (data.topics[t] || []).forEach(s => {
              stories.push({ ...s, link: s.url || s.link || '', topic: t });
            });
          });
        }
        state.discoverStories = stories;
      }
    } catch (e) {
      console.warn('Discover cache unavailable:', e.message);
    }

    if (state.userAddedStories.length > 0) {
      state.discoverStories = [...state.userAddedStories, ...state.discoverStories];
    }

    state.discoverVisible = 10;
    renderDiscoverColumn();
  }

  async function fetchFeed(feedId, cfg) {
    const allItems = [];

    for (let i = 0; i < cfg.urls.length; i++) {
      const url = cfg.urls[i];
      const subLabel = cfg.subLabels ? cfg.subLabels[i] : null;
      try {
        const res = await fetch(RSS2JSON_API + encodeURIComponent(url));
        if (!res.ok) continue;
        const data = await res.json();

        (data.items || []).forEach(item => {
          const entry = {
            title: item.title || '',
            link: item.link || '',
            description: (item.description || '').replace(/<[^>]*>/g, '').slice(0, 300),
            pubDate: item.pubDate || '',
            author: item.author || '',
            source: cfg.name
          };
          if (subLabel) entry.subFeed = subLabel;
          allItems.push(entry);
        });
      } catch (e) {
        console.warn('Feed fetch failed:', url, e.message);
      }
    }

    const seen = new Set();
    const deduped = allItems.filter(item => {
      const key = (item.title || '').toLowerCase().trim();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });

    state.feedData[feedId] = deduped;
    state.feedVisible[feedId] = 10;
    if (feedId === 'wsj') state.wsjAllItems = deduped;
  }

  async function fetchAllFeeds() {
    const promises = Object.entries(FEED_CONFIGS)
      .filter(([, cfg]) => cfg.enabled)
      .map(([id, cfg]) => fetchFeed(id, cfg));
    await Promise.all(promises);
    renderAllFeeds();
  }

  async function fetchCustomFeed(cf) {
    try {
      const res = await fetch(RSS2JSON_API + encodeURIComponent(cf.url));
      if (!res.ok) throw new Error('Fetch error');
      const data = await res.json();

      state.feedData[cf.id] = (data.items || []).map(item => ({
        title: item.title || '',
        link: item.link || '',
        description: (item.description || '').replace(/<[^>]*>/g, '').slice(0, 300),
        pubDate: item.pubDate || '',
        author: item.author || '',
        source: cf.name
      }));
      state.feedVisible[cf.id] = 10;
    } catch (e) {
      console.warn('Custom feed error:', e.message);
      state.feedData[cf.id] = [];
    }
  }

  // ========== ACTIONS ==========
  window.refreshAll = async function() {
    Object.keys(FEED_CONFIGS).forEach(id => {
      const body = document.getElementById(id + '-body');
      if (body) body.innerHTML = '<div class="column-status"><div class="spinner"></div>Refreshing\u2026</div>';
    });
    document.getElementById('discover-body').innerHTML = '<div class="column-status"><div class="spinner"></div>Refreshing\u2026</div>';

    state.discoverVisible = 10;
    Object.keys(state.feedVisible).forEach(k => { state.feedVisible[k] = 10; });
    state.gapVisible = 10;

    fetchDiscover();
    await fetchAllFeeds();

    for (const cf of state.customFeeds) {
      if (cf.enabled) {
        await fetchCustomFeed(cf);
        renderFeedColumn(cf.id);
      }
    }

    if (state.feedData['wsj']) state.wsjAllItems = state.feedData['wsj'];
  };

  window.toggleHighlightGaps = function() {
    state.highlightGaps = !state.highlightGaps;
    const btn = document.getElementById('btn-highlight');
    btn.classList.toggle('active', state.highlightGaps);
    renderAllFeeds();
  };

  window.setWsjFilter = function(filter, el) {
    state.wsjFilter = filter;
    state.feedVisible['wsj'] = 10;
    document.querySelectorAll('#wsj-filters .filter-pill').forEach(p => p.classList.remove('active'));
    el.classList.add('active');

    if (filter === 'all') {
      state.feedData['wsj'] = state.wsjAllItems;
    } else {
      state.feedData['wsj'] = state.wsjAllItems.filter(s => s.subFeed === filter);
    }
    renderFeedColumn('wsj');
  };

  window.setDiscoverFilter = function(filter, el) {
    state.discoverFilter = filter;
    state.discoverVisible = 10;
    document.querySelectorAll('#discover-filters .filter-pill').forEach(p => p.classList.remove('active'));
    el.classList.add('active');
    renderDiscoverColumn();
  };

  window.showMoreDiscover = function() {
    state.discoverVisible += 5;
    renderDiscoverColumn();
  };

  window.showMoreFeed = function(feedId) {
    state.feedVisible[feedId] = (state.feedVisible[feedId] || 10) + 5;
    renderFeedColumn(feedId);
  };

  window.showMoreGaps = function() {
    state.gapVisible += 5;
    renderGapAnalysis();
  };

  window.toggleSource = function(id) {
    FEED_CONFIGS[id].enabled = !FEED_CONFIGS[id].enabled;
    savePreferences();
    renderSourceManager();

    const col = document.getElementById('col-' + id);
    if (col) col.style.display = FEED_CONFIGS[id].enabled ? '' : 'none';
    if (FEED_CONFIGS[id].enabled) renderFeedColumn(id);
  };

  window.toggleCustomSource = function(id) {
    const cf = state.customFeeds.find(f => f.id === id);
    if (cf) {
      cf.enabled = !cf.enabled;
      savePreferences();
      renderSourceManager();
      const col = document.getElementById('col-' + id);
      if (col) col.style.display = cf.enabled ? '' : 'none';
    }
  };

  window.addCustomFeed = function() {
    const input = document.getElementById('custom-feed-url');
    const url = (input.value || '').trim();
    if (!url) return;

    const id = 'custom_' + state.customFeeds.length;
    let name;
    try { name = 'Custom: ' + new URL(url).hostname; } catch { name = 'Custom Feed'; }
    const cf = { id, name, url, enabled: true };
    state.customFeeds.push(cf);
    savePreferences();

    createCustomColumn(cf);
    fetchCustomFeed(cf).then(() => renderFeedColumn(cf.id));
    input.value = '';
    renderSourceManager();
  };

  function createCustomColumn(cf) {
    const main = document.querySelector('.main-container');
    const col = document.createElement('div');
    col.className = 'column';
    col.id = 'col-' + cf.id;
    col.innerHTML = `
      <div class="column-header">
        <div class="column-title">
          <span class="source-dot" style="background:var(--accent-blue)"></span>
          ${escapeHtml(cf.name)}
          <span class="count-badge" id="${cf.id}-count">0</span>
        </div>
      </div>
      <div class="column-body" id="${cf.id}-body">
        <div class="column-status"><div class="spinner"></div>Loading\u2026</div>
      </div>
    `;
    main.appendChild(col);
  }

  // ========== QUICK PASTE ==========
  window.setPasteTopic = function(topic) {
    state.pasteTopic = topic;
    ['top', 'tech', 'finance'].forEach(t => {
      document.getElementById('paste-topic-' + t).classList.toggle('active', t === topic);
    });
  };

  window.submitPaste = function() {
    const input = document.getElementById('paste-input');
    const lines = (input.value || '').split('\n').map(l => l.trim()).filter(Boolean);

    const newStories = lines.map(title => ({
      title,
      link: '',
      description: '',
      pubDate: new Date().toISOString(),
      source: 'Perplexity Discover',
      topic: state.pasteTopic
    }));

    state.userAddedStories = [...newStories, ...state.userAddedStories];
    state.discoverStories = [...newStories, ...state.discoverStories];
    savePreferences();

    input.value = '';
    closeOverlay('quick-paste');
    state.discoverVisible = 10;
    renderDiscoverColumn();
    if (state.highlightGaps) renderAllFeeds();
  };

  // ========== OVERLAYS ==========
  window.openOverlay = function(id) {
    if (state.currentOverlay === id) {
      closeOverlay(id);
      return;
    }

    if (state.currentOverlay) {
      document.getElementById('overlay-' + state.currentOverlay).classList.remove('visible');
    }

    if (id === 'gap-analysis') {
      state.gapVisible = 10;
      renderGapAnalysis();
    } else if (id === 'add-sources') {
      renderSourceManager();
    } else if (id === 'social-pulse') {
      setTimeout(() => {
        if (window.twttr && window.twttr.widgets) window.twttr.widgets.load();
      }, 100);
    }

    document.getElementById('overlay-' + id).classList.add('visible');
    state.currentOverlay = id;
  };

  window.closeOverlay = function(id) {
    document.getElementById('overlay-' + id).classList.remove('visible');
    if (state.currentOverlay === id) state.currentOverlay = null;
  };

  window.closeOverlayBackdrop = function(e) {
    if (e.target.classList.contains('overlay-backdrop') && state.currentOverlay) {
      closeOverlay(state.currentOverlay);
    }
  };

  // ========== KEYBOARD SHORTCUTS ==========
  document.addEventListener('keydown', function(e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
      if (e.key === 'Escape' && state.currentOverlay) {
        closeOverlay(state.currentOverlay);
      }
      return;
    }

    switch (e.key.toLowerCase()) {
      case 'r': refreshAll(); break;
      case 'h': toggleHighlightGaps(); break;
      case 'g': openOverlay('gap-analysis'); break;
      case 's': openOverlay('social-pulse'); break;
      case 'a': openOverlay('add-sources'); break;
      case 'i': openOverlay('guide'); break;
      case 'p': openOverlay('quick-paste'); break;
      case 'escape':
        if (state.currentOverlay) closeOverlay(state.currentOverlay);
        break;
    }
  });

  // ========== INIT ==========
  async function init() {
    loadSaved();

    // Recreate any custom feed columns from saved preferences
    state.customFeeds.forEach(cf => {
      if (cf.enabled) createCustomColumn(cf);
    });

    // Hide disabled built-in columns
    Object.entries(FEED_CONFIGS).forEach(([id, cfg]) => {
      if (!cfg.enabled) {
        const col = document.getElementById('col-' + id);
        if (col) col.style.display = 'none';
      }
    });

    // Fetch everything
    fetchDiscover();
    await fetchAllFeeds();

    if (state.feedData['wsj']) state.wsjAllItems = state.feedData['wsj'];

    for (const cf of state.customFeeds) {
      if (cf.enabled) {
        await fetchCustomFeed(cf);
        renderFeedColumn(cf.id);
      }
    }
  }

  init();
})();
