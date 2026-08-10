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
