$(document).ready(function() {
    // Function to update the download count and queue
    function updateDownloadCountAndQueue() {
        $.get('/api/queue_len', function(data) {
            $('#download-count').text(data.count);
        });

        $.get('/api/queue', function(data) {
            $('#queue-dropdown').empty();
            if (data.queue.length > 0) {
                data.queue.forEach(function(item) {
                    $('#queue-dropdown').append('<li><a class="dropdown-item" href="#">' + item + '</a></li>');
                });
            } else {
                $('#queue-dropdown').append('<li><a class="dropdown-item" href="#">Nothing in the queue!</a></li>');
            }
        });
    }

    // Initial update of download count and queue
    updateDownloadCountAndQueue();
    // Periodically update the download count and queue every 10 seconds (you can adjust this interval)
    setInterval(updateDownloadCountAndQueue, 10000);
});