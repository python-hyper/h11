// This code is Copyright 2012 Statsmodels Developers
//
// It is licensed under the GNU Public License version 3
// (available at https://www.gnu.org/licenses/gpl.txt)
//
// It was originally taken from the Statsmodels projet
// (https://github.com/statsmodels/statsmodels)

function htmlescape(text){
    return (text.replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;"))
}

function scrapeText(codebox){
    /// Returns input lines cleaned of prompt1 and prompt2
    var lines = codebox.split('\n');
    var newlines = new Array();
    $.each(lines, function() {
        if (this.match(/^In \[\d+]: /)){
            newlines.push(this.replace(/^(\s)*In \[\d+]: /,""));
        }
        else if (this.match(/^(\s)*\.+:/)){
            newlines.push(this.replace(/^(\s)*\.+: /,""));
        }

    }
            );
    return newlines.join('\\n');
}

$(document).ready(            
        function() {
    // grab all code boxes
    var ipythoncode = $(".highlight-ipython");
    $.each(ipythoncode, function() {
        var code = scrapeText($(this).text());
        // give them a facebox pop-up with plain text code   
        $(this).append('<span style="text-align:right; display:block; margin-top:-10px; margin-left:10px; font-size:60%"><a href="javascript: jQuery.facebox(\'<textarea cols=80 rows=10 readonly style=margin:5px onmouseover=javascript:this.select();>'+htmlescape(htmlescape(code))+'</textarea>\');">View Code</a></span>');
        $(this,"textarea").select();
    });
});
