$(document).ready(function() {
    function formatMB(bytes) {
        return (bytes / 1048576).toFixed(1) + ' MB';
    }

    function getVideoId(filename) {
        var parts = filename.replace('.MP4', '').split('_');
        var id = parts.length >= 4 ? parts[3] : (parts[1] || filename);
        return id.replace(/^0+/, '');
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

                $('#queue-dropdown').append(
                    '<li>' +
                    '<div class="px-3 py-2" style="max-width:280px;">' +
                    '<small style="color:#00b4d8;"><span class="queue-pulse">&#9679;</span> Downloading</small><br>' +
                    '<small class="d-block" style="color:#eceff4;">' + dateLabel + '</small>' +
                    '<div class="progress mt-1" style="height: 4px; background:#21262d;">' +
                    '<div class="progress-bar progress-bar-striped progress-bar-animated" role="progressbar" style="width:' + barWidth + '; background:#00b4d8;"></div>' +
                    '</div>' +
                    '<small style="color:#484f58;">' + sizeLabel + '</small>' +
                    '</div>' +
                    '</li>'
                );

                if (pendingQueue.length > 0) {
                    $('#queue-dropdown').append('<li><hr class="dropdown-divider" style="border-color:#21262d;"></li>');
                }
            }

            if (pendingQueue.length > 0) {
                pendingQueue.forEach(function(item) {
                    var fname = item.split('/').pop();
                    var dateLabel = getVideoId(fname);
                    $('#queue-dropdown').append('<li><a class="dropdown-item" style="max-width:280px; color:#c9d1d9;" href="#"><i class="fas fa-hourglass-half" style="color:#484f58;"></i> ' + dateLabel + '</a></li>');
                });
            } else if (!progress.active) {
                $('#queue-dropdown').append('<li><a class="dropdown-item" style="color:#484f58;" href="#"><i class="fas fa-check-circle" style="color:#00b4d8;"></i> Queue empty</a></li>');
            }
        });
    }

    updateQueueAndProgress();
    setInterval(updateQueueAndProgress, 5000);
});
