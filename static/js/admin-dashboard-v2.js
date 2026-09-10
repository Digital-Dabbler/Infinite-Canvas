(function () {
  'use strict';

  var activeWorkspace = 'overview';
  var activeUserDepartment = 'all';
  var selectedUserIds = new Set();
  var expandedUsageId = '';
  var departmentsReady = false;
  var workspaceElements = {};
  var baseDrawDonut = window.drawDonut;
  var baseDrawTrend = window.drawTrend;

  window.drawDonut = function () {
    var canvas = document.querySelector('#statusChart');
    var bounds = canvas?.getBoundingClientRect();
    if (!bounds || bounds.width < 48 || bounds.height < 48) return;
    return baseDrawDonut();
  };

  window.drawTrend = function () {
    var canvas = document.querySelector('#trendChart');
    var bounds = canvas?.getBoundingClientRect();
    if (!bounds || bounds.width < 96 || bounds.height < 64) return;
    return baseDrawTrend();
  };

  function exactDepartmentId(user) {
    var stored = String(user?.department_id || '');
    if (stored && departmentRows.some(function (row) { return row.id === stored; })) return stored;
    var legacy = String(user?.department || '').trim();
    var match = departmentRows.find(function (row) { return String(row.name || '').trim() === legacy; });
    return match ? match.id : '';
  }

  function sectionFor(selector) {
    return document.querySelector(selector)?.closest('section') || null;
  }

  function installWorkspaceNavigation() {
    var nav = document.createElement('nav');
    nav.className = 'admin-workspaces';
    nav.setAttribute('aria-label', '管理页分区');
    [
      ['overview', '概览与部门对比'],
      ['accounts', '用户与配置'],
      ['usage', '调用排障'],
      ['system', '公告发布']
    ].forEach(function (entry) {
      var button = document.createElement('button');
      button.type = 'button';
      button.dataset.workspace = entry[0];
      button.textContent = entry[1];
      button.onclick = function () { switchWorkspace(entry[0]); };
      nav.appendChild(button);
    });
    document.querySelector('header.admin-masthead').after(nav);

    workspaceElements = {
      overview: [
        document.querySelector('.dashboard-head'), document.querySelector('.cards'),
        document.querySelector('.dashboard-grid'), document.querySelector('.usage-spectrum')
      ],
      accounts: [sectionFor('#departments'), sectionFor('#apiProfiles'), sectionFor('#users')],
      usage: [sectionFor('#events'), sectionFor('#alerts')],
      system: [sectionFor('#announcementTitle')]
    };
  }

  function switchWorkspace(name) {
    activeWorkspace = name;
    Object.entries(workspaceElements).forEach(function (entry) {
      entry[1].filter(Boolean).forEach(function (node) {
        node.classList.toggle('admin-v2-hidden', entry[0] !== name);
      });
    });
    document.querySelectorAll('[data-workspace]').forEach(function (button) {
      button.setAttribute('aria-selected', String(button.dataset.workspace === name));
    });
    if (name === 'overview') {
      if (usageAnalytics && Object.keys(usageAnalytics).length) {
        renderDashboard();
        renderServiceHealth();
      }
      loadDepartmentComparison();
      loadProviderBalances();
    }
  }

  function installOverviewPanels() {
    var tools = document.createElement('div');
    tools.className = 'admin-overview-tools';
    tools.innerHTML = '<div><strong>数据观察范围</strong><small><br>总览与单部门使用同一时间窗口</small></div>' +
      '<label>部门视角<select id="overviewDepartment"><option value="">全部部门</option></select></label>';
    document.querySelector('.dashboard-head').after(tools);
    workspaceElements.overview.push(tools);
    tools.querySelector('select').onchange = async function () {
      usagePage = 1;
      await Promise.all([loadAnalytics(), loadUsage()]);
    };

    var balances = document.createElement('section');
    balances.className = 'panel';
    balances.innerHTML = '<div class="chart-title"><div><h2>平台余额</h2><small>按 API 配置组列出所有已启用平台</small></div><button id="refreshBalances">刷新余额</button></div><div id="providerBalances" class="balance-grid"><div class="detail-empty">正在读取平台状态…</div></div>';
    document.querySelector('.cards').after(balances);
    workspaceElements.overview.push(balances);
    balances.querySelector('#refreshBalances').onclick = loadProviderBalances;

    var comparison = document.createElement('section');
    comparison.className = 'panel department-compare';
    comparison.innerHTML = '<div class="chart-title"><div><h2>部门横向对比</h2><small>用户、调用量、成功率、失败与模型类型构成</small></div><small id="departmentCompareRange"></small></div><div id="departmentComparison" class="detail-empty">正在汇总部用量…</div>';
    document.querySelector('.usage-spectrum').after(comparison);
    workspaceElements.overview.push(comparison);
  }

  async function loadProviderBalances() {
    var target = document.querySelector('#providerBalances');
    if (!target || activeWorkspace !== 'overview') return;
    target.innerHTML = '<div class="detail-empty">正在查询余额…</div>';
    try {
      var data = await get('/api/admin/provider-balances');
      var rows = data.balances || [];
      var groups = new Map();
      rows.forEach(function (row) {
        var id = row.api_profile_id || '__unknown__';
        if (!groups.has(id)) groups.set(id, {
          id: id, name: row.api_profile_name || id, rows: []
        });
        groups.get(id).rows.push(row);
      });
      target.innerHTML = Array.from(groups.values()).map(function (group) {
        var configured = group.rows.filter(function (row) { return row.configured; });
        var hidden = group.rows.filter(function (row) { return !row.configured; });
        var accountRows = configured.map(function (row) {
          var value = row.status === 'ok'
            ? (row.balance == null || row.balance === '' ? '余额已连接' : String(row.balance) + (row.currency ? ' ' + row.currency : ''))
            : row.status === 'unsupported' ? '已配置 · 暂无余额接口' : '查询失败';
          var meta = row.status === 'ok'
            ? [row.coins == null ? '' : 'RH 币 ' + row.coins, '运行中 ' + (row.running_tasks || 0)].filter(Boolean).join(' · ')
            : row.message || '';
          return '<article class="balance-account ' + esc(row.status) + '"><div class="balance-account-name"><strong>' +
            esc(row.provider_name) + '</strong><small>' + esc(row.provider_id) + '</small></div><div><div class="balance-value">' +
            esc(value) + '</div><div class="balance-meta">' + esc(meta) + '</div></div></article>';
        }).join('');
        var empty = configured.length ? '' : '<div class="balance-profile-empty">尚未配置可用平台，展开下方列表可查看全部候选平台。</div>';
        var collapsed = hidden.length ? '<details class="balance-hidden"><summary>' + hidden.length +
          ' 个平台尚未配置</summary><div class="balance-hidden-list">' + hidden.map(function (row) {
            return '<span class="balance-hidden-item">' + esc(row.provider_name) + '</span>';
          }).join('') + '</div></details>' : '';
        return '<article class="balance-profile"><header class="balance-profile-head"><div class="balance-profile-title"><strong>' +
          esc(group.name) + '</strong><small>' + esc(group.id) + '</small></div><span class="balance-profile-count">' +
          configured.length + ' 已配置 / ' + group.rows.length + ' 平台</span></header>' +
          (configured.length ? '<div class="balance-accounts">' + accountRows + '</div>' : empty) + collapsed + '</article>';
      }).join('') || '<div class="detail-empty">当前配置组中没有启用的平台</div>';
    } catch (error) {
      target.innerHTML = '<div class="detail-empty bad">' + esc(error.message || '余额读取失败') + '</div>';
    }
  }

  async function loadDepartmentComparison() {
    var target = document.querySelector('#departmentComparison');
    if (!target || activeWorkspace !== 'overview') return;
    updateRange();
    var scopeWindow = effectiveWindow();
    var params = new URLSearchParams({
      start_at: scopeWindow.start_at,
      end_at: addMinutes(scopeWindow.end_at, 1)
    });
    try {
      var data = await get('/api/admin/usage/departments?' + params);
      var rows = (data.departments || []).filter(function (row) {
        return row.department_id !== '__unassigned__';
      });
      target.className = '';
      target.innerHTML = '<table class="compare-table"><thead><tr><th>部门</th><th>成员</th><th>活跃</th><th>调用</th><th>成功率</th><th>失败</th><th>平均耗时</th><th>图像 / 视频 / LLM</th></tr></thead><tbody>' +
        rows.map(function (row) {
          var categories = (row.image || 0) + (row.video || 0) + (row.llm || 0);
          var width = function (value) { return categories ? (value / categories * 100) : 0; };
          return '<tr><td class="compare-name"><strong>' + esc(row.department) + '</strong><small>' + esc(row.department_id) +
            '</small></td><td>' + row.users + '</td><td>' + row.active_users + '</td><td><strong>' + row.total +
            '</strong></td><td class="' + (row.success_rate >= .9 ? 'ok' : row.total ? 'warn' : '') + '">' +
            (row.total ? (row.success_rate * 100).toFixed(1) + '%' : '—') + '</td><td class="' + (row.failed ? 'bad' : '') +
            '">' + row.failed + '</td><td>' + formatDuration(row.average_duration_ms) + '</td><td>' +
            '<div class="compare-bars" title="图像 ' + row.image + ' / 视频 ' + row.video + ' / LLM ' + row.llm + '">' +
            '<i class="image" style="width:' + width(row.image) + '%"></i><i class="video" style="width:' + width(row.video) +
            '%"></i><i class="llm" style="width:' + width(row.llm) + '%"></i></div><small>图 ' + row.image + ' · 视 ' +
            row.video + ' · LLM ' + row.llm + '</small></td></tr>';
        }).join('') + '</tbody></table>';
      document.querySelector('#departmentCompareRange').textContent = scopeWindow.start_at.replace('T', ' ') + ' — ' + scopeWindow.end_at.replace('T', ' ');
    } catch (error) {
      target.className = 'detail-empty bad';
      target.textContent = error.message || '部门对比读取失败';
    }
  }

  function renderDepartmentTabs() {
    var host = document.querySelector('#departmentUserTabs');
    if (!host) return;
    var counts = {};
    userRows.filter(function (user) { return user.role !== 'admin'; }).forEach(function (user) {
      var id = exactDepartmentId(user);
      counts[id] = (counts[id] || 0) + 1;
    });
    var entries = [['all', '全部', userRows.filter(function (user) { return user.role !== 'admin'; }).length]]
      .concat(departmentRows.map(function (row) { return [row.id, row.name, counts[row.id] || 0]; }));
    host.innerHTML = entries.map(function (entry) {
      return '<button type="button" class="' + (activeUserDepartment === entry[0] ? 'active' : '') +
        '" data-user-department="' + esc(entry[0]) + '">' + esc(entry[1]) + ' <small>' + entry[2] + '</small></button>';
    }).join('') + '<button type="button" class="add-department" id="quickAddDepartment">＋ 添加部门</button>';
    host.querySelectorAll('[data-user-department]').forEach(function (button) {
      button.onclick = function () {
        activeUserDepartment = button.dataset.userDepartment;
        selectedUserIds.clear();
        renderUserProfileAssignments();
      };
    });
    host.querySelector('#quickAddDepartment').onclick = async function () {
      var name = prompt('新部门名称');
      if (!name?.trim()) return;
      try {
        await send('/api/admin/departments', 'POST', { name: name.trim() });
        showMessage('部门已新增。');
        await load();
      } catch (error) {
        showMessage(error.message || '新增部门失败。', true);
      }
    };
  }

  function installUserControls() {
    var section = sectionFor('#users');
    var tabs = document.createElement('div');
    tabs.id = 'departmentUserTabs';
    tabs.className = 'department-tabs';
    section.querySelector('h2').after(tabs);
    var toolbar = document.createElement('div');
    toolbar.className = 'bulk-toolbar';
    toolbar.innerHTML = '<span class="selection-count" id="bulkSelectionCount">已选择 0 位用户</span>' +
      '<select id="bulkProfile"><option value="">未分配（禁止付费调用）</option></select>' +
      '<button id="applyBulkProfile" class="primary" disabled>批量调整配置组</button>';
    section.querySelector('.table-wrap').before(toolbar);
    section.querySelector('thead th:first-child').innerHTML = '<input id="selectVisibleUsers" class="user-check" type="checkbox" aria-label="选择当前部门全部用户"> 姓名 / 账号';
    toolbar.querySelector('#applyBulkProfile').onclick = applyBulkProfile;
    section.querySelector('#selectVisibleUsers').onchange = function (event) {
      visibleUsers().forEach(function (user) {
        if (event.target.checked) selectedUserIds.add(user.id);
        else selectedUserIds.delete(user.id);
      });
      renderUserProfileAssignments();
    };
  }

  function visibleUsers() {
    return userRows.filter(function (user) {
      if (user.role === 'admin') return activeUserDepartment === 'all';
      return activeUserDepartment === 'all' || exactDepartmentId(user) === activeUserDepartment;
    });
  }

  function v2RenderUserProfileAssignments() {
    if (!document.querySelector('#departmentUserTabs')) return;
    renderDepartmentTabs();
    var profiles = '<option value="">未分配（禁止付费调用）</option>' + apiProfileRows.map(function (profile) {
      return '<option value="' + esc(profile.id) + '"' + (!profile.enabled ? ' disabled' : '') + '>' +
        esc(profile.name) + (!profile.enabled ? '（已停用）' : '') + '</option>';
    }).join('');
    document.querySelector('#bulkProfile').innerHTML = profiles;
    var rows = visibleUsers();
    var profileOptions = function (user) {
      return '<option value="">未分配（禁止付费调用）</option>' + apiProfileRows.map(function (profile) {
        return '<option value="' + esc(profile.id) + '" ' + (user.api_profile_id === profile.id ? 'selected' : '') + ' ' +
          (!profile.enabled && user.api_profile_id !== profile.id ? 'disabled' : '') + '>' + esc(profile.name) +
          (!profile.enabled ? '（已停用）' : '') + '</option>';
      }).join('');
    };
    var departmentOptions = function (user) {
      if (user.role === 'admin') return '<option value="">管理（管理员）</option>';
      return departmentRows.map(function (department) {
        return '<option value="' + esc(department.id) + '" ' + (exactDepartmentId(user) === department.id ? 'selected' : '') + ' ' +
          (!department.enabled && exactDepartmentId(user) !== department.id ? 'disabled' : '') + '>' + esc(department.name) +
          (!department.enabled ? '（已停用）' : '') + '</option>';
      }).join('');
    };
    document.querySelector('#users').innerHTML = rows.map(function (user) {
      return '<tr><td><input class="user-check" data-user-check="' + esc(user.id) + '" type="checkbox" ' +
        (selectedUserIds.has(user.id) ? 'checked' : '') + ' ' + (user.role === 'admin' ? 'disabled' : '') + '> ' +
        esc(user.name) + '<small><br>' + esc(user.username) + '</small></td><td><select data-department-user="' + user.id +
        '" ' + (user.role === 'admin' ? 'disabled' : '') + '>' + departmentOptions(user) + '</select><button onclick="saveUserDepartment(\'' +
        user.id + '\')" ' + (user.role === 'admin' ? 'disabled' : '') + '>分配</button></td><td><select data-profile-user="' +
        user.id + '">' + profileOptions(user) + '</select><button onclick="saveProfileAssignment(\'' + user.id +
        '\')">分配</button></td><td>' + esc(user.role) + '</td><td>' + (user.enabled ? '启用' : '停用') +
        '</td><td><div class="quota-grid"><label class="quota-field">图像<input data-quota="daily_image" data-user="' + user.id +
        '" type="number" min="0" value="' + (user.quota?.daily_image || 0) + '"></label><label class="quota-field">视频<input data-quota="daily_video" data-user="' +
        user.id + '" type="number" min="0" value="' + (user.quota?.daily_video || 0) + '"></label><label class="quota-field">LLM<input data-quota="daily_llm" data-user="' +
        user.id + '" type="number" min="0" value="' + (user.quota?.daily_llm || 0) + '"></label></div></td><td><div class="actions"><button onclick="saveQuota(\'' +
        user.id + '\')">保存配额</button><button onclick="toggle(\'' + user.id + '\',' + (!user.enabled) + ')">' +
        (user.enabled ? '停用' : '启用') + '</button><button onclick="openPasswordReset(\'' + user.id +
        '\')">重置密码</button><button class="danger" onclick="deleteUser(\'' + user.id + '\')">删除</button></div></td></tr>';
    }).join('') || '<tr><td colspan="7"><small>该部门暂无用户</small></td></tr>';
    document.querySelectorAll('[data-user-check]').forEach(function (checkbox) {
      checkbox.onchange = function () {
        if (checkbox.checked) selectedUserIds.add(checkbox.dataset.userCheck);
        else selectedUserIds.delete(checkbox.dataset.userCheck);
        updateBulkState();
      };
    });
    updateBulkState();
  }

  function updateBulkState() {
    var count = selectedUserIds.size;
    document.querySelector('#bulkSelectionCount').textContent = '已选择 ' + count + ' 位用户';
    document.querySelector('#applyBulkProfile').disabled = !count;
    var selectable = visibleUsers().filter(function (user) { return user.role !== 'admin'; });
    document.querySelector('#selectVisibleUsers').checked = !!selectable.length && selectable.every(function (user) {
      return selectedUserIds.has(user.id);
    });
  }

  async function applyBulkProfile() {
    if (!selectedUserIds.size) return;
    var profileId = document.querySelector('#bulkProfile').value;
    try {
      var result = await send('/api/admin/users/bulk-profile', 'PATCH', {
        user_ids: Array.from(selectedUserIds), api_profile_id: profileId
      });
      selectedUserIds.clear();
      showMessage('已批量调整 ' + result.updated + ' 位用户的配置组。');
      await load();
    } catch (error) {
      showMessage(error.message || '批量调整失败。', true);
    }
  }

  function detailMeta(event) {
    return '<dl class="usage-detail-meta"><dt>事件 ID</dt><dd>' + esc(event.id || '—') +
      '</dd><dt>用户</dt><dd>' + esc([event.name, event.username].filter(Boolean).join(' · ')) +
      '</dd><dt>配置组</dt><dd>' + esc(event.api_profile_name || event.api_profile_id || '—') +
      '</dd><dt>平台 / 模型</dt><dd>' + esc(modelName(event)) +
      '</dd><dt>本地任务</dt><dd>' + esc(event.detail?.task_id || '—') +
      '</dd><dt>上游任务</dt><dd>' + esc(event.detail?.upstream_task_id || event.upstream_task_id || '—') +
      '</dd><dt>耗时</dt><dd>' + esc(formatDuration(event.duration_ms)) + '</dd></dl>';
  }

  function v2RenderEvents(rows) {
    document.querySelector('#events').innerHTML = rows.map(function (event) {
      var open = expandedUsageId === event.id;
      var detail = event.detail || {};
      var outputs = detail.outputs || [];
      var media = outputs.length ? '<div class="usage-output-grid">' + outputs.map(function (output) {
        var mediaNode = output.kind === 'video'
          ? '<video src="' + esc(output.url) + '" controls preload="metadata"></video>'
          : '<img src="' + esc(output.url) + '" alt="生成结果" loading="lazy">';
        return '<a href="' + esc(output.url) + '" target="_blank" rel="noopener">' + mediaNode + '</a>';
      }).join('') + '</div>' : '';
      var error = detail.error || event.error || '';
      var diagnostic = error
        ? '<div class="usage-error"><pre>' + esc(error) + '</pre><button type="button" data-copy-error="' +
          encodeURIComponent(error) + '">复制错误</button></div>'
        : media || '<div class="detail-empty">' + (event.status === 'succeeded' ? '本条成功记录没有可预览的站内媒体' : '暂无更多任务详情') + '</div>';
      return '<tr class="usage-main-row ' + (open ? 'expanded' : '') + '" tabindex="0" data-usage-row="' + esc(event.id) +
        '" aria-expanded="' + String(open) + '"><td>' + esc(event.created_at_iso) + '</td><td>' +
        esc(event.name || event.username) + '<small><br>' + esc(event.department) + '</small></td><td>' +
        esc(event.api_profile_name || event.api_profile_id || '未标明') + '<small class="profile-usage-note">' +
        esc(event.api_profile_id || '') + ' · <span class="billing-badge">' + esc(billingLabel(event.billing_scope)) +
        '</span>' + (event.credential_kind === 'runninghub_wallet' ? ' · RunningHub 钱包' : '') + '</small></td><td>' +
        esc(event.client_source) + '</td><td>' + esc(event.function) + '<small><br>' + esc(modelName(event)) +
        '</small></td><td class="' + (['failed', 'timed_out'].includes(event.status) ? 'bad' : event.status === 'succeeded' ? 'ok' : 'warn') +
        '">' + esc(statusLabel(event.status)) + '</td><td>' + formatDuration(event.duration_ms) + '</td></tr>' +
        (open ? '<tr class="usage-detail-row"><td colspan="7"><div class="usage-detail">' + detailMeta(event) +
          '<div>' + diagnostic + '</div></div></td></tr>' : '');
    }).join('') || '<tr><td colspan="7"><small>没有符合条件的用量记录</small></td></tr>';
    document.querySelectorAll('[data-usage-row]').forEach(function (row) {
      var toggle = function () {
        expandedUsageId = expandedUsageId === row.dataset.usageRow ? '' : row.dataset.usageRow;
        v2RenderEvents(rows);
      };
      row.onclick = toggle;
      row.onkeydown = function (event) {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggle(); }
      };
    });
    document.querySelectorAll('[data-copy-error]').forEach(function (button) {
      button.onclick = async function (event) {
        event.stopPropagation();
        var text = decodeURIComponent(button.dataset.copyError || '');
        try {
          await navigator.clipboard.writeText(text);
          button.textContent = '已复制';
        } catch (_) {
          var area = document.createElement('textarea');
          area.value = text; document.body.appendChild(area); area.select(); document.execCommand('copy'); area.remove();
          button.textContent = '已复制';
        }
      };
    });
  }

  function shortScopeValue(value) {
    return String(value || '').slice(5).replace('T', ' ');
  }

  function scopeQuickRanges() {
    var now = new Date();
    var today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var startOfWeek = new Date(today.getTime() - ((today.getDay() + 6) % 7) * 86400000);
    var startOfMonth = new Date(now.getFullYear(), now.getMonth(), 1);
    var startOfLastMonth = new Date(now.getFullYear(), now.getMonth() - 1, 1);
    return [
      ['最近1小时', new Date(now.getTime() - 3600000), now],
      ['最近24小时', new Date(now.getTime() - 86400000), now],
      ['今天', today, now],
      ['昨天', new Date(today.getTime() - 86400000), new Date(today.getTime() - 60000)],
      ['本周', startOfWeek, now],
      ['本月', startOfMonth, now],
      ['上月', startOfLastMonth, new Date(startOfMonth.getTime() - 60000)]
    ];
  }

  function scopeSummaryValue(summary, key) {
    summary = summary || {};
    if (key === 'total') return +summary.total || 0;
    return (+summary.failed || 0) + (+summary.timed_out || 0) + (+summary.cancelled || 0);
  }

  function scopeDeltaSuffix(current, previous) {
    if (!previous) return '';
    var delta = current - previous;
    if (!delta) return '　＝ 持平';
    return '　' + (delta > 0 ? '▲' : '▼') + ' ' + (Math.round(Math.abs(delta) / previous * 1000) / 10) + '%';
  }

  function renderScopeComparison() {
    var cards = document.querySelector('.cards');
    if (!cards) return;
    [['total', '#metricTotal', '总调用'], ['failed', '#metricFailed', '失败']].forEach(function (entry) {
      var metric = cards.querySelector(entry[1]);
      var card = metric?.closest('.card');
      if (!card) return;
      var host = card.querySelector('[data-scope-delta="' + entry[0] + '"]');
      if (!host) {
        host = document.createElement('div');
        host.className = 'scope-delta';
        host.setAttribute('data-scope-delta', entry[0]);
        card.appendChild(host);
      }
      var text = '';
      if (rangeState.compare) {
        var current = scopeSummaryValue(usageAnalytics?.summary, entry[0]);
        text = usagePrevious
          ? entry[2] + '：上一周期 ' + scopeSummaryValue(usagePrevious.summary, entry[0]).toLocaleString() + ' 条' +
            scopeDeltaSuffix(current, scopeSummaryValue(usagePrevious.summary, entry[0]))
          : entry[2] + '：对比区间无数据';
      }
      host.textContent = text;
      host.hidden = !text;
    });
  }

  function updateScopeHint() {
    var hint = document.querySelector('#scopeHint');
    var error = document.querySelector('#scopeError');
    var start = document.querySelector('#customStart')?.value || '';
    var end = document.querySelector('#customEnd')?.value || '';
    if (!hint) return;
    var message = validateUsageRange(start, end);
    var minutes = Math.round((new Date(end).getTime() - new Date(start).getTime()) / 60000);
    var spanText = isFinite(minutes) && minutes > 0
      ? '当前跨度 ' + (minutes >= 1440 ? Math.round(minutes / 1440) + ' 天 ' : '') +
        (minutes % 1440 >= 60 ? Math.round((minutes % 1440) / 60) + ' 小时 ' : '') + (minutes % 60) + ' 分'
      : '请选择开始与结束时间';
    hint.textContent = spanText + '　下限 1 小时，上限 ' + Math.round(scopeMaxMinutes() / 1440) + ' 天（受审计保留期限制）';
    if (error) {
      error.textContent = message;
      error.hidden = !message;
    }
    var apply = document.querySelector('#customApply');
    if (apply) apply.disabled = !!message;
  }

  function closeScopePopover() {
    var popover = document.querySelector('#scopePopover');
    var toggle = document.querySelector('#customRangeToggle');
    if (popover) popover.hidden = true;
    if (toggle) toggle.setAttribute('aria-expanded', 'false');
  }

  function toggleScopePopover() {
    var popover = document.querySelector('#scopePopover');
    var toggle = document.querySelector('#customRangeToggle');
    if (!popover) return;
    var open = popover.hidden;
    popover.hidden = !open;
    if (toggle) toggle.setAttribute('aria-expanded', String(open));
    if (open) {
      var startInput = popover.querySelector('#customStart');
      if (startInput) {
        startInput.value = rangeState.custom_start || rangeState.start_at;
        popover.querySelector('#customEnd').value = rangeState.custom_end || rangeState.end_at;
        updateScopeHint();
        startInput.focus();
      }
    }
  }

  function scopeEchoHtml() {
    var html = '当前区间 <b>' + esc(usageScopeLabel()) + '</b> · 共 ' + esc(usageSpanLabel()) + ' · ' + esc(usageBucketLabel());
    if (rangeState.compare) {
      var previous = usagePreviousWindow();
      html += ' · 对比区间 ' + esc(previous.start_at.replace('T', ' ')) + ' ～ ' + esc(previous.end_at.replace('T', ' '));
    }
    if (chartState.start_at) {
      html += ' · <button type="button" class="scope-back" id="scopeDrillBack">已下钻，返回上一级</button>';
    }
    html += '<small>审计保留 ' + scopeRetentionDays() + ' 天 · 可查最早 ' + esc(scopeEarliestMinute().replace('T', ' ')) + '</small>';
    return html;
  }

  function syncScopeBar() {
    var bar = document.querySelector('#usageScopeBar');
    if (!bar) return;
    bar.querySelectorAll('[data-time-preset]').forEach(function (button) {
      var active = rangeState.mode === 'preset' && button.dataset.timePreset === rangeState.key;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    var custom = bar.querySelector('#customRangeToggle');
    if (custom) {
      custom.classList.toggle('active', rangeState.mode === 'custom');
      custom.textContent = rangeState.mode === 'custom'
        ? '自定义 ' + shortScopeValue(rangeState.custom_start) + ' ～ ' + shortScopeValue(rangeState.custom_end)
        : '自定义…';
    }
    var granularity = bar.querySelector('#scopeGranularity');
    if (granularity) granularity.value = rangeState.granularity;
    var compare = bar.querySelector('#scopeCompare');
    if (compare) {
      compare.classList.toggle('active', !!rangeState.compare);
      compare.setAttribute('aria-pressed', String(!!rangeState.compare));
    }
    var echo = bar.querySelector('#scopeEcho');
    if (echo) echo.innerHTML = scopeEchoHtml();
    var back = document.querySelector('#scopeDrillBack');
    if (back) back.onclick = function () { exitUsageDrill(); };
    var legend = document.querySelector('#legendCompare');
    if (legend) legend.hidden = !rangeState.compare;
    var startInput = bar.querySelector('#customStart');
    var endInput = bar.querySelector('#customEnd');
    if (startInput && document.activeElement !== startInput) startInput.value = rangeState.custom_start || rangeState.start_at;
    if (endInput && document.activeElement !== endInput) endInput.value = rangeState.custom_end || rangeState.end_at;
    updateScopeHint();
  }

  function installTimeScopeBar() {
    if (document.querySelector('#usageScopeBar')) return;
    var bar = document.createElement('section');
    bar.className = 'usage-scope-bar';
    bar.id = 'usageScopeBar';
    bar.innerHTML =
      '<div class="scope-field"><span class="scope-label">时间范围</span><div class="scope-presets" role="group" aria-label="时间范围预设" id="scopePresets"></div></div>' +
      '<div class="scope-field"><span class="scope-label">聚合粒度</span><select id="scopeGranularity" aria-label="聚合粒度"></select></div>' +
      '<div class="scope-field"><span class="scope-label">&nbsp;</span><button type="button" id="scopeCompare" class="scope-toggle" aria-pressed="false">对比上一周期</button></div>' +
      '<div class="scope-field"><span class="scope-label">&nbsp;</span><button type="button" id="scopeRefresh" class="scope-plain">刷新</button></div>' +
      '<div class="scope-echo" id="scopeEcho"></div>' +
      '<div class="scope-popover" id="scopePopover" hidden><h4>自定义时间范围</h4>' +
      '<div class="scope-poprow"><label>开始<input type="datetime-local" id="customStart"></label><label>结束<input type="datetime-local" id="customEnd"></label></div>' +
      '<div class="scope-quick" id="scopeQuick"></div><div class="scope-hint" id="scopeHint"></div><div class="scope-error" id="scopeError" hidden></div>' +
      '<div class="scope-popactions"><button type="button" id="customCancel" class="scope-plain">取消</button><button type="button" id="customApply" class="scope-apply">应用</button></div></div>';
    document.querySelector('nav.admin-workspaces').after(bar);

    var presets = bar.querySelector('#scopePresets');
    usagePresetList().forEach(function (preset) {
      var button = document.createElement('button');
      button.type = 'button';
      button.setAttribute('data-time-preset', preset.key);
      button.textContent = preset.label;
      button.onclick = function () { closeScopePopover(); selectUsagePreset(preset.key); };
      presets.appendChild(button);
    });
    var custom = document.createElement('button');
    custom.type = 'button';
    custom.id = 'customRangeToggle';
    custom.className = 'scope-custom';
    custom.setAttribute('aria-expanded', 'false');
    custom.textContent = '自定义…';
    custom.onclick = toggleScopePopover;
    presets.appendChild(custom);

    var granularity = bar.querySelector('#scopeGranularity');
    usageGranularityList().forEach(function (option) {
      var node = document.createElement('option');
      node.value = option.value;
      node.textContent = option.value === 'auto' ? '自动' : option.label;
      granularity.appendChild(node);
    });
    granularity.onchange = function () { setUsageGranularity(granularity.value); };

    bar.querySelector('#scopeCompare').onclick = function () { toggleUsageCompare(); };
    bar.querySelector('#scopeRefresh').onclick = function () { refreshUsageViews(); };

    var quick = bar.querySelector('#scopeQuick');
    scopeQuickRanges().forEach(function (entry) {
      var button = document.createElement('button');
      button.type = 'button';
      button.textContent = entry[0];
      button.onclick = function () {
        bar.querySelector('#customStart').value = localTimeValue(entry[1]);
        bar.querySelector('#customEnd').value = localTimeValue(entry[2]);
        updateScopeHint();
      };
      quick.appendChild(button);
    });

    var startInput = bar.querySelector('#customStart');
    var endInput = bar.querySelector('#customEnd');
    startInput.oninput = updateScopeHint;
    endInput.oninput = updateScopeHint;
    bar.querySelector('#customCancel').onclick = closeScopePopover;
    bar.querySelector('#customApply').onclick = async function () {
      var error = await applyUsageCustomRange(startInput.value, endInput.value);
      if (error) {
        var errorNode = bar.querySelector('#scopeError');
        errorNode.textContent = error;
        errorNode.hidden = false;
        return;
      }
      closeScopePopover();
    };
    window.renderUsageScopeBar = syncScopeBar;
    syncScopeBar();
  }

  installWorkspaceNavigation();
  installTimeScopeBar();
  installOverviewPanels();
  installUserControls();

  var baseParamsForUsage = window.paramsForUsage;
  window.paramsForUsage = function (includePage) {
    var params = baseParamsForUsage(includePage);
    var department = document.querySelector('#overviewDepartment')?.value || '';
    if (department) params.set('department_id', department);
    return params;
  };

  window.loadAnalytics = async function () {
    if (analyticsPromise) return analyticsPromise;
    analyticsPromise = (async function () {
      updateRange();
      var scopeWindow = effectiveWindow();
      var params = new URLSearchParams({
        start_at: scopeWindow.start_at, end_at: addMinutes(scopeWindow.end_at, 1),
        bucket_minutes: rangeState.bucket, user_limit: 10, model_limit: 6, dimension_limit: 10
      });
      var selected = document.querySelector('#modelUsageUser')?.value || '';
      var profile = document.querySelector('#usageProfile')?.value || '';
      var billing = document.querySelector('#usageBilling')?.value || '';
      var department = document.querySelector('#overviewDepartment')?.value || '';
      if (selected) params.set('selected_user_id', selected);
      if (profile) params.set('api_profile_id', profile);
      if (billing) params.set('billing_scope', billing);
      if (department) params.set('department_id', department);
      var previousParams = null;
      if (rangeState.compare) {
        var previousWindow = usagePreviousWindow();
        previousParams = new URLSearchParams(params);
        previousParams.set('start_at', previousWindow.start_at);
        previousParams.set('end_at', addMinutes(previousWindow.end_at, 1));
      }
      var analyticsResults = await Promise.all([
        get('/api/admin/usage/analytics?' + params),
        previousParams ? get('/api/admin/usage/analytics?' + previousParams) : Promise.resolve(null)
      ]);
      usageAnalytics = analyticsResults[0];
      setUsagePrevious(analyticsResults[1]);
      analyticsRows = usageAnalytics.summary?.total ? [{}] : [];
      if (activeWorkspace === 'overview') {
        renderDashboard();
        renderScopeComparison();
        renderServiceHealth();
        loadDepartmentComparison();
      }
      return usageAnalytics;
    })();
    try { return await analyticsPromise; } finally { analyticsPromise = null; }
  };

  window.renderEvents = v2RenderEvents;
  window.renderUserProfileAssignments = v2RenderUserProfileAssignments;

  var baseLoad = window.load;
  window.load = async function () {
    var result = await baseLoad();
    var select = document.querySelector('#overviewDepartment');
    var current = select.value;
    select.innerHTML = '<option value="">全部部门</option>' + departmentRows.map(function (row) {
      return '<option value="' + esc(row.id) + '">' + esc(row.name) + '</option>';
    }).join('');
    select.value = current;
    departmentsReady = true;
    v2RenderUserProfileAssignments();
    return result;
  };

  switchWorkspace('overview');
  var readyTimer = window.setInterval(function () {
    if (!departmentRows.length || !apiProfileRows.length || !userRows.length) return;
    window.clearInterval(readyTimer);
    var select = document.querySelector('#overviewDepartment');
    select.innerHTML = '<option value="">全部部门</option>' + departmentRows.map(function (row) {
      return '<option value="' + esc(row.id) + '">' + esc(row.name) + '</option>';
    }).join('');
    departmentsReady = true;
    v2RenderUserProfileAssignments();
    loadDepartmentComparison();
  }, 150);
})();
