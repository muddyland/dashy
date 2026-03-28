$(document).ready(function() {
    function formatMB(bytes) {
        return (bytes / 1048576).toFixed(1) + ' MB';
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
                var barClass = progress.total_bytes > 0
                    ? 'progress-bar progress-bar-striped progress-bar-animated bg-success'
                    : 'progress-bar progress-bar-striped progress-bar-animated bg-success w-100';
                var sizeLabel = progress.total_bytes > 0
                    ? pct + '% &mdash; ' + formatMB(progress.bytes_downloaded) + ' / ' + formatMB(progress.total_bytes)
                    : formatMB(progress.bytes_downloaded) + ' downloaded';

                $('#queue-dropdown').append(
                    '<li>' +
                    '<div class="px-3 py-2" style="max-width:280px;">' +
                    '<small class="text-white-50">Downloading:</small><br>' +
                    '<small class="d-block text-truncate">' + progress.filename + '</small>' +
                    '<div class="progress mt-1" style="height: 6px;">' +
                    '<div class="' + barClass + '" role="progressbar" style="width:' + barWidth + '"></div>' +
                    '</div>' +
                    '<small class="text-white-50">' + sizeLabel + '</small>' +
                    '</div>' +
                    '</li>'
                );

                if (pendingQueue.length > 0) {
                    $('#queue-dropdown').append('<li><hr class="dropdown-divider"></li>');
                }
            }

            if (pendingQueue.length > 0) {
                pendingQueue.forEach(function(item) {
                    var fname = item.split('/').pop();
                    $('#queue-dropdown').append('<li><a class="dropdown-item text-truncate d-block" style="max-width:280px;" href="#">' + fname + '</a></li>');
                });
            } else if (!progress.active) {
                $('#queue-dropdown').append('<li><a class="dropdown-item" href="#">Nothing in the queue!</a></li>');
            }
        });
    }

    updateQueueAndProgress();
    setInterval(updateQueueAndProgress, 5000);
});
