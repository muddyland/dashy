// Shared confirm/notify dialog backed by the modal in main.html. Defined
// outside document.ready so any page's inline script can call it.
window.dashyModal = (function() {
    function els() {
        var root = document.getElementById('dashy-modal');
        if (!root) return null;
        return {
            root: root,
            title: document.getElementById('dashy-modal-title'),
            body: document.getElementById('dashy-modal-body'),
            note: document.getElementById('dashy-modal-note'),
            cancel: document.getElementById('dashy-modal-cancel'),
            confirm: document.getElementById('dashy-modal-confirm')
        };
    }

    function show(opts) {
        var e = els();
        // No modal markup on this page (or Bootstrap missing): fall back so a
        // confirmation is never silently skipped.
        if (!e || typeof bootstrap === 'undefined') {
            if (opts.onConfirm) {
                if (window.confirm(opts.body)) opts.onConfirm();
            } else {
                window.alert(opts.body);
            }
            return;
        }

        e.title.textContent = opts.title || '';
        e.title.style.color = opts.danger ? '#e57373' : '#00b4d8';
        e.body.textContent = opts.body || '';

        if (opts.note) {
            e.note.textContent = opts.note;
            e.note.style.display = '';
        } else {
            e.note.style.display = 'none';
        }

        // Replace the confirm button to drop any handler from a previous open.
        var confirmBtn = e.confirm.cloneNode(true);
        e.confirm.parentNode.replaceChild(confirmBtn, e.confirm);

        var modal = bootstrap.Modal.getOrCreateInstance(e.root);

        if (opts.onConfirm) {
            e.cancel.style.display = '';
            e.cancel.textContent = opts.cancelText || 'Cancel';
            confirmBtn.className = 'btn ' + (opts.danger ? 'btn-danger' : 'btn-primary');
            confirmBtn.textContent = opts.confirmText || 'Confirm';
            confirmBtn.addEventListener('click', function() {
                modal.hide();
                opts.onConfirm();
            });
        } else {
            // Notification: single dismiss button, no cancel.
            e.cancel.style.display = 'none';
            confirmBtn.className = 'btn btn-secondary';
            confirmBtn.textContent = opts.confirmText || 'Close';
            confirmBtn.setAttribute('data-bs-dismiss', 'modal');
        }

        modal.show();
    }

    return {
        confirm: function(opts) { show(opts); },
        notify: function(opts) {
            show({
                title: opts.title,
                body: opts.body,
                note: opts.note,
                danger: opts.danger,
                confirmText: opts.confirmText
            });
        }
    };
})();

$(document).ready(function() {
    function formatMB(bytes) {
        return (bytes / 1048576).toFixed(1) + ' MB';
    }

    function getVideoId(filename) {
        var parts = filename.replace('.MP4', '').split('_');
        var id = parts.length >= 4 ? parts[3] : (parts[1] || filename);
        return id.replace(/^0+/, '');
    }

    // Queue maintenance. Loop recording overwrites clips, so a queue built up
    // over time can contain entries the camera no longer has; these can never
    // download and just sit there.
    function queueAction(url, done) {
        $.ajax({url: url, method: 'POST', contentType: 'application/json'})
            .done(function(res) { done(res); updateQueueAndProgress(); })
            .fail(function(xhr) {
                var msg = (xhr.responseJSON && xhr.responseJSON.error) || 'request failed';
                dashyModal.notify({
                    title: 'Queue not updated',
                    body: msg,
                    danger: true
                });
            });
    }

    function plural(n, one, many) {
        return n + ' ' + (n === 1 ? one : many);
    }

    function appendQueueActions() {
        var $prune = $('<a class="dropdown-item small" href="#">')
            .css('color', '#00b4d8')
            .append($('<i class="fas fa-broom">'), ' Remove clips no longer on camera')
            .on('click', function(e) {
                e.preventDefault();
                queueAction('/api/queue/prune', function(res) {
                    dashyModal.notify({
                        title: 'Queue tidied',
                        body: res.removed
                            ? 'Removed ' + plural(res.removed, 'clip', 'clips') +
                              ' the camera no longer has.'
                            : 'Nothing to remove — every queued clip is still on the camera.',
                        note: plural(res.remaining, 'clip', 'clips') + ' still queued.'
                    });
                });
            });

        var $clear = $('<a class="dropdown-item small" href="#">')
            .css('color', '#e57373')
            .append($('<i class="fas fa-trash">'), ' Clear queue')
            .on('click', function(e) {
                e.preventDefault();
                dashyModal.confirm({
                    title: 'Clear queue',
                    body: 'Empty the download queue?',
                    note: 'Downloaded clips are kept. Anything still on the camera can be queued again.',
                    danger: true,
                    confirmText: 'Clear queue',
                    onConfirm: function() {
                        queueAction('/api/queue/clear', function(res) {
                            dashyModal.notify({
                                title: 'Queue cleared',
                                body: 'Removed ' + plural(res.cleared, 'clip', 'clips') + ' from the queue.'
                            });
                        });
                    }
                });
            });

        $('#queue-dropdown')
            .append($('<li><hr class="dropdown-divider" style="border-color:#21262d;"></li>'))
            .append($('<li>').append($prune))
            .append($('<li>').append($clear));
    }

    function updateQueueAndProgress() {
        $.when(
            $.get('/api/queue'),
            $.get('/api/progress')
        ).done(function(queueResp, progressResp) {
            var queue = queueResp[0].queue;
            var progress = progressResp[0];

            // If a file is actively downloading it's still in the queue — filter it out
            var pendingQueue = queue;
            if (progress.active) {
                pendingQueue = queue.filter(function(item) { return item !== progress.url; });
            }

            var totalCount = pendingQueue.length + (progress.active ? 1 : 0);
            $('#download-count').text(totalCount);

            $('#queue-dropdown').empty();

            if (progress.active) {
                var pct = progress.percent || 0;
                var barWidth = progress.total_bytes > 0 ? pct + '%' : '100%';
                var sizeLabel = progress.total_bytes > 0
                    ? pct + '% &mdash; ' + formatMB(progress.bytes_downloaded) + ' / ' + formatMB(progress.total_bytes)
                    : formatMB(progress.bytes_downloaded) + ' downloaded';
                var dateLabel = getVideoId(progress.filename);

                // Filenames come from the camera, so labels are set as text
                // rather than concatenated into an HTML string.
                var $item = $('<li>').append(
                    $('<div class="px-3 py-2">').css('max-width', '280px').append(
                        $('<small>').css('color', '#00b4d8').append(
                            $('<span class="queue-pulse">').html('&#9679;'), ' Downloading'),
                        $('<br>'),
                        $('<small class="d-block">').css('color', '#eceff4').text(dateLabel),
                        $('<div class="progress mt-1">').css({height: '4px', background: '#21262d'}).append(
                            $('<div class="progress-bar progress-bar-striped progress-bar-animated" role="progressbar">')
                                .css({width: barWidth, background: '#00b4d8'})),
                        $('<small>').css('color', '#484f58').html(sizeLabel)
                    )
                );
                $('#queue-dropdown').append($item);

                if (pendingQueue.length > 0) {
                    $('#queue-dropdown').append('<li><hr class="dropdown-divider" style="border-color:#21262d;"></li>');
                }
            }

            if (totalCount > 0) {
                appendQueueActions();
            }

            if (pendingQueue.length > 0) {
                pendingQueue.forEach(function(item) {
                    var fname = item.split('/').pop();
                    var dateLabel = getVideoId(fname);
                    $('#queue-dropdown').append(
                        $('<li>').append(
                            $('<a class="dropdown-item" href="#">')
                                .css({'max-width': '280px', color: '#c9d1d9'})
                                .append($('<i class="fas fa-hourglass-half">').css('color', '#484f58'), ' ')
                                .append(document.createTextNode(dateLabel))
                        )
                    );
                });
            } else if (!progress.active) {
                $('#queue-dropdown').append('<li><a class="dropdown-item" style="color:#484f58;" href="#"><i class="fas fa-check-circle" style="color:#00b4d8;"></i> Queue empty</a></li>');
            }
        });
    }

    updateQueueAndProgress();
    setInterval(updateQueueAndProgress, 5000);
});
