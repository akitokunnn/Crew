// Process fixture for the stdio bridge. No network, credentials, or model use.
using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Collections.Generic;
using System.Web.Script.Serialization;
class FakeCodexDesktopNative {
    static JavaScriptSerializer Json=new JavaScriptSerializer();
    static HashSet<string> Loaded=new HashSet<string>();
    static string Mode="idle";
    static bool SuppressClosed;
    static void Output(object value) {Console.WriteLine(Json.Serialize(value));Console.Out.Flush();}
    static int Main(string[] args) {
        Console.InputEncoding=new UTF8Encoding(false);Console.OutputEncoding=new UTF8Encoding(false);
        if(args.Contains("--version")){Console.Write("codex-fixture 1.0\r\n");return 0;}
        if(args.Contains("--print-startup-config")){Console.Write("{\"policy\":\"native\"}\n");Console.Error.Write("native startup stderr\n");return 0;}
        File.WriteAllText(Environment.GetEnvironmentVariable("FAKE_NATIVE_ARGS"),Json.Serialize(args));
        string line;
        while((line=Console.ReadLine())!=null) {
            var p=Json.Deserialize<Dictionary<string,object>>(line);
            if(!p.ContainsKey("method"))continue;
            var method=(string)p["method"];
            var data=p.ContainsKey("params") ? p["params"] as Dictionary<string,object> : null;
            if(data==null)data=new Dictionary<string,object>();
            var thread=data.ContainsKey("threadId") ? data["threadId"] as string : null;
            File.AppendAllText(Environment.GetEnvironmentVariable("FAKE_NATIVE_PACKETS"),line+"\n");
            if(method=="test/exit")return 23;
            if(method=="initialized")continue;
            object result=new {};
            if(method=="initialize") {
                var capabilities=data.ContainsKey("capabilities") ? data["capabilities"] as Dictionary<string,object> : null;
                var excluded=capabilities!=null && capabilities.ContainsKey("optOutNotificationMethods") ? capabilities["optOutNotificationMethods"] as System.Collections.IEnumerable : null;
                SuppressClosed=excluded!=null && excluded.Cast<object>().Any(value => (value as string)=="thread/closed");
                result=new {userAgent="fake"};
            }
            else if(method=="test/mode")Mode=(string)data["value"];
            else if(method=="thread/start" || method=="thread/resume") {
                if(thread==null)thread=Guid.NewGuid().ToString();
                Loaded.Add(thread);result=new {thread=new {id=thread,status=new {type="idle"},turns=new object[0]}};
                if(Mode=="pending")Output(new {id="pending-approval",method="item/commandExecution/requestApproval",@params=new {threadId=thread}});
                if(Mode=="inflight")continue;
            } else if(method=="thread/read") {
                if(Mode=="timeout")continue;
                var state=Mode=="active" ? "active" : Loaded.Contains(thread) ? "idle" : "notLoaded";
                result=new {thread=new {id=thread,status=new {type=state},turns=new object[0]}};
            } else if(method=="thread/loaded/list") {
                if(Mode=="paginated" && !data.ContainsKey("cursor"))result=new {data=new string[0],nextCursor="page-two"};
                else result=new {data=Loaded.ToArray(),nextCursor=(string)null};
            }
            else if(method=="turn/start" && !Loaded.Contains(thread)) {Output(new {id=p["id"],error=new {code=-32600,message="thread not found: "+thread}});continue;}
            else if(method=="thread/unsubscribe") {
                if(Mode=="unsubscribe_error") {Output(new {id=p["id"],error=new {code=-32600,message="native refused unsubscribe"}});continue;}
                Loaded.Remove(thread);
                if(Mode!="no_closed" && !SuppressClosed)Output(new {method="thread/closed",@params=new {threadId=thread}});
            } else if(method=="test/resolved")Output(new {method="serverRequest/resolved",@params=new {threadId=thread,requestId="pending-approval"}});
            if(p.ContainsKey("id"))Output(new {id=p["id"],result=result});
        }
        File.WriteAllText(Environment.GetEnvironmentVariable("FAKE_NATIVE_EXIT"),"stdin eof");
        return 0;
    }
}
