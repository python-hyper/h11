// Stolen from statsmodels and fixed up
// Here's what statsmodels' LICENSE.txt says:
//
// Copyright (C) 2006, Jonathan E. Taylor
// All rights reserved.
//
// Copyright (c) 2006-2008 Scipy Developers.
// All rights reserved.
//
// Copyright (c) 2009-2012 Statsmodels Developers.
// All rights reserved.
//
//
// Redistribution and use in source and binary forms, with or without
// modification, are permitted provided that the following conditions are met:
//
//   a. Redistributions of source code must retain the above copyright notice,
//      this list of conditions and the following disclaimer.
//   b. Redistributions in binary form must reproduce the above copyright
//      notice, this list of conditions and the following disclaimer in the
//      documentation and/or other materials provided with the distribution.
//   c. Neither the name of Statsmodels nor the names of its contributors
//      may be used to endorse or promote products derived from this software
//      without specific prior written permission.
//
//
// THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
// AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
// IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
// ARE DISCLAIMED. IN NO EVENT SHALL STATSMODELS OR CONTRIBUTORS BE LIABLE FOR
// ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
// DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
// SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
// CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
// LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
// OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH
// DAMAGE.


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
