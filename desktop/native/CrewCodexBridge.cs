// Official Codex desktop adapter. Native stdio remains point-to-point.
// A token-authenticated loopback control channel yields only exactly bound idle threads.
// It never archives threads, modifies native history, starts model turns or changes permissions.
using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Collections;
using System.Collections.Generic;
using System.Web.Script.Serialization;

class CrewCodexBridge {
    static readonly object State = new object(), ToNative = new object(), ToDesktop = new object(), AuditLock = new object();
    static readonly Dictionary<string, TaskCompletionSource<Dictionary<string,object>>> Pending = new Dictionary<string, TaskCompletionSource<Dictionary<string,object>>>();
    static readonly HashSet<string> Blocked = new HashSet<string>(), Closed = new HashSet<string>();
    static readonly HashSet<string> Initializing = new HashSet<string>();
    static Process Native;
    static string Nonce = "crew-control-" + Guid.NewGuid().ToString("N");
    static int Counter;
    static bool Initialized;
    static bool DesktopSuppressesClosed;
    class Busy : Exception { public Busy(string message):base(message) {} }
    static string BridgeRoot, Token;
    static readonly Dictionary<string,string> NativeRequests = new Dictionary<string,string>();
    static readonly Dictionary<string,string> Loading = new Dictionary<string,string>();
    static readonly Dictionary<string,Dictionary<string,object>> ResumeRequests = new Dictionary<string,Dictionary<string,object>>();
    static readonly HashSet<string> Restoring = new HashSet<string>();
    static JavaScriptSerializer Serializer() { return new JavaScriptSerializer { MaxJsonLength = Int32.MaxValue }; }
    static string Serialize(object value) { return Serializer().Serialize(value); }
    static Dictionary<string,object> Parse(string text) { return Serializer().Deserialize<Dictionary<string,object>>(text); }
    static string Env(string key) { return Environment.GetEnvironmentVariable(key); }
    static int Main(string[] args) {
        try { return Run(args).GetAwaiter().GetResult(); }
        catch (Exception e) { Audit(new { type="proxy_failure", error=e.Message }); Console.Error.WriteLine(e.GetType().Name + ": " + e.Message); return 1; }
    }
    static async Task<int> Run(string[] args) {
        Console.InputEncoding = new UTF8Encoding(false); Console.OutputEncoding = new UTF8Encoding(false);
        string real = Env("CREW_CODEX_REAL_CLI");
        BridgeRoot = Env("CREW_CODEX_BRIDGE_ROOT");
        if (String.IsNullOrEmpty(real)) throw new InvalidOperationException("Missing native CLI");
        bool stream = args.Contains("app-server") && !args.Contains("--print-startup-config") && !args.Contains("--help") && !args.Contains("generate-json-schema") && !args.Contains("generate-ts");
        if(stream && String.IsNullOrEmpty(BridgeRoot)) throw new InvalidOperationException("Missing bridge root");
        var forwarded = stream ? new [] {"-c", "thread_unload_delay_secs=0"}.Concat(args).ToArray() : args;
        var start = new ProcessStartInfo(real, String.Join(" ", forwarded.Select(Quote))) {
            UseShellExecute=false, CreateNoWindow=true, RedirectStandardOutput=true,
            RedirectStandardError=true, RedirectStandardInput=stream
        };
        start.EnvironmentVariables["CODEX_CLI_PATH"] = real;
        Native = Process.Start(start);
        var errorCopy = Native.StandardError.BaseStream.CopyToAsync(Console.OpenStandardError());
        if (!stream) {
            var outputCopy = Native.StandardOutput.BaseStream.CopyToAsync(Console.OpenStandardOutput());
            Native.WaitForExit(); await Task.WhenAll(errorCopy, outputCopy); return Native.ExitCode;
        }
        Directory.CreateDirectory(Path.Combine(BridgeRoot,"instances"));
        Audit(new { type="proxy_started", proxy_pid=Process.GetCurrentProcess().Id, native_pid=Native.Id });

        var incoming = Task.Run(() => ReadNative());
        var control = Task.Run(() => ControlLoop());
        var outgoing = Task.Run(() => ReadDesktop());
        try {
            await Task.WhenAny(incoming, outgoing, control);
            if(control.IsFaulted) await control;
            if(outgoing.IsFaulted) await outgoing;
            if(incoming.IsFaulted) await incoming;
        } finally {
            try { Native.StandardInput.Close(); } catch {}
            if (!Native.WaitForExit(5000)) { Native.Kill(); Native.WaitForExit(); }
            try { File.Delete(Path.Combine(BridgeRoot,"instances",Nonce+".json")); } catch {}
        }
        await incoming; await errorCopy; return Native.ExitCode;
    }
    static async Task ReadDesktop() {
            string line;
            while ((line = await Console.In.ReadLineAsync()) != null) {
                var packet = Parse(line);
                string method = packet.ContainsKey("method") ? packet["method"] as string : null;
                var parameters = packet.ContainsKey("params") ? packet["params"] as Dictionary<string,object> : null;
                string thread = parameters != null && parameters.ContainsKey("threadId") ? parameters["threadId"] as string : null;
                if(method=="turn/start" && thread!=null && !IsGated(thread) && Binding(thread)!=null) {
                    try { await Restore(thread, false); }
                    catch(Exception error) {
                        if(packet.ContainsKey("id"))SendDesktop(Serialize(new {id=packet["id"],error=new {code=-32001,message=error.Message}}));
                        continue;
                    }
                }
                bool reject = false;
                lock (State) {
                    reject = thread != null && IsGated(thread) && WritesThread(method);
                    if (!reject && method == "thread/resume" && parameters != null && parameters.ContainsKey("path")) {
                        var path = parameters["path"] as string;
                        reject = path != null && Directory.GetFiles(Path.Combine(BridgeRoot,"bindings"),"*.json").Any(file => path.Contains(Path.GetFileNameWithoutExtension(file)) && IsGated(Path.GetFileNameWithoutExtension(file)));
                    }
                    if (!reject) {
                        if(packet.ContainsKey("id") && method == "initialize") {
                            Initializing.Add(Serialize(packet["id"]));
                            var capabilities=parameters!=null && parameters.ContainsKey("capabilities") ? parameters["capabilities"] as Dictionary<string,object> : null;
                            var excluded=capabilities!=null && capabilities.ContainsKey("optOutNotificationMethods") ? capabilities["optOutNotificationMethods"] as IEnumerable : null;
                            if(excluded!=null && excluded.Cast<object>().Any(value => (value as string)=="thread/closed")) {
                                // Desktop opts out of this notification. The adapter needs the
                                // real native close acknowledgement, then honors that opt-out
                                // when forwarding to Desktop. All other capabilities survive.
                                DesktopSuppressesClosed=true;
                                capabilities["optOutNotificationMethods"]=excluded.Cast<object>().Where(value => (value as string)!="thread/closed").ToArray();
                                line=Serialize(packet);
                            }
                        }
                        if(method == "thread/resume" && thread != null) {
                            if(Binding(thread)!=null && packet.ContainsKey("id")) {
                                ResumeRequests[Serialize(packet["id"])]=Parse(Serialize(parameters));
                                packet["params"]=DesktopParameters(thread,parameters);
                                line=Serialize(packet);
                            }
                        }
                        if (packet.ContainsKey("id") && WritesThread(method) && thread != null) Loading[Serialize(packet["id"])]=thread;
                        if (packet.ContainsKey("id") && method == null) NativeRequests.Remove(Serialize(packet["id"]));
                        SendNative(line);
                        if(method == "initialized") Initialized=true;
                    }
                }
                if (reject && packet.ContainsKey("id")) {
                    Audit(new { type="desktop_write_blocked", method=method, threadId=thread, requestId=packet["id"] });
                    SendDesktop(Serialize(new { id=packet["id"], error=new {code=-32001, message="This thread has been handed back to Crew. Return it to Codex before continuing here."} }));
                }
            }
    }
    static bool WritesThread(string method) {
        if (method == null) return false;
        if (method.StartsWith("turn/") || method.StartsWith("thread/realtime/")) return true;
        return method == "thread/start" || method == "thread/startAeon" || method == "thread/revert" || method == "thread/stop" || method == "thread/resume" || method == "thread/shellCommand" || method == "thread/compact/start" ||
            method == "thread/rollback" || method == "thread/archive" || method == "thread/unarchive" || method == "thread/settings/update";
    }
    static void SendNative(string line) { lock (ToNative) { Native.StandardInput.WriteLine(line); Native.StandardInput.Flush(); } }
    static void SendDesktop(string line) { lock (ToDesktop) { Console.Out.WriteLine(line); Console.Out.Flush(); } }
    static async Task ReadNative() {
        string line;
        while ((line = await Native.StandardOutput.ReadLineAsync()) != null) {
            var packet = Parse(line);
            var id = packet.ContainsKey("id") ? packet["id"] as string : null;
            TaskCompletionSource<Dictionary<string,object>> waiter = null;
            lock (State) { if (id != null && Pending.TryGetValue(id, out waiter)) Pending.Remove(id); }
            if (waiter != null) { waiter.TrySetResult(packet); continue; }
            if(id != null && id.StartsWith(Nonce+":")) continue; // Expired control reply never belongs to Desktop.
            Dictionary<string,object> resumed=null;
            lock(State) {
                if(packet.ContainsKey("id") && !packet.ContainsKey("method") && Initializing.Remove(Serialize(packet["id"])) && !packet.ContainsKey("error")) Initialized=true;
                if(packet.ContainsKey("id") && !packet.ContainsKey("method")) Loading.Remove(Serialize(packet["id"]));
                if(packet.ContainsKey("id") && !packet.ContainsKey("method") && ResumeRequests.TryGetValue(Serialize(packet["id"]),out resumed)) ResumeRequests.Remove(Serialize(packet["id"]));
                if(packet.ContainsKey("id") && packet.ContainsKey("method")) {
                    var rp=packet.ContainsKey("params") ? packet["params"] as Dictionary<string,object> : null;
                    NativeRequests[Serialize(packet["id"])]=rp != null && rp.ContainsKey("threadId") ? rp["threadId"] as string : "*";
                }
            }
            if(resumed!=null && !packet.ContainsKey("error")) {
                string expected=(string)resumed["threadId"];
                var result=packet["result"] as Dictionary<string,object>;
                if(ResultThread(result)!=expected)line=Serialize(new {id=packet["id"],error=new {code=-32001,message="Native resume returned a different thread; Crew binding was not changed."}});
                else SaveResume(expected,resumed,"loaded");
            }
            if (packet.ContainsKey("method") && (string)packet["method"] == "serverRequest/resolved") {
                var parameters=packet["params"] as Dictionary<string,object>;
                lock(State) NativeRequests.Remove(Serialize(parameters["requestId"]));
            }
            if (packet.ContainsKey("method") && (string)packet["method"] == "thread/closed") {
                var parameters = packet["params"] as Dictionary<string,object>;
                var thread = parameters["threadId"] as string;
                lock (State) Closed.Add(thread);
                Audit(new {type="thread_closed",threadId=thread});
                if(DesktopSuppressesClosed) continue;
            }
            SendDesktop(line);
        }
    }
    static async Task<Dictionary<string,object>> Request(string method, object parameters, int timeoutMs=5000) {
        var id = Nonce + ":" + Interlocked.Increment(ref Counter);
        // A cold-resume continuation may read Desktop stdin again. Never run
        // it inline on the native reader, which must keep draining replies.
        var waiter = new TaskCompletionSource<Dictionary<string,object>>(TaskCreationOptions.RunContinuationsAsynchronously);
        lock (State) Pending[id] = waiter;
        SendNative(Serialize(new { id=id, method=method, @params=parameters }));
        if (await Task.WhenAny(waiter.Task, Task.Delay(timeoutMs)) != waiter.Task) { lock(State) Pending.Remove(id); throw new TimeoutException(method); }
        var packet = await waiter.Task;
        if (packet.ContainsKey("error")) throw new InvalidOperationException(method + ": " + Serialize(packet["error"]));
        return packet["result"] as Dictionary<string,object>;
    }
    static async Task ControlLoop() {
        var random=new byte[32]; using(var rng=RandomNumberGenerator.Create()) rng.GetBytes(random);
        Token=Convert.ToBase64String(random);
        var listener=new TcpListener(IPAddress.Loopback,0); listener.Start();
        int port=((IPEndPoint)listener.LocalEndpoint).Port;
        var registration=new {protocol=1,pid=Process.GetCurrentProcess().Id,nonce=Nonce,native_home=Env("CODEX_HOME"),host="127.0.0.1",port=port,token=Token,native_pid=Native.Id};
        File.WriteAllText(Path.Combine(BridgeRoot,"instances",Nonce+".json"),Serialize(registration),new UTF8Encoding(false));
        try {
            while(!Native.HasExited) {
                using(var connection=await listener.AcceptTcpClientAsync())
                using(var stream=connection.GetStream())
                using(var reader=new StreamReader(stream,new UTF8Encoding(false),false,8192,true))
                using(var writer=new StreamWriter(stream,new UTF8Encoding(false),8192,true){AutoFlush=true}) {
                    object reply;
                    string replyNative=null, replyScope=null;
                    try {
                        var lineTask=reader.ReadLineAsync();
                        if(await Task.WhenAny(lineTask,Task.Delay(5000))!=lineTask) throw new TimeoutException("control request");
                        var request=Parse(await lineTask);
                        if(!request.ContainsKey("token") || (string)request["token"]!=Token || !request.ContainsKey("nonce") || (string)request["nonce"]!=Nonce) throw new UnauthorizedAccessException("Invalid control capability");
                        string operation=(string)request["action"], thread=request.ContainsKey("native_id")? request["native_id"] as string:null;
                        replyNative=thread; replyScope=request.ContainsKey("scope_id")? request["scope_id"] as string:null;
                        var binding=Binding(thread);
                        if(binding==null || replyScope!=(string)binding["scope_id"]) throw new InvalidOperationException("Unknown thread binding or mismatched scope");
                        object result;
                        if(operation=="ping") {
                            result=new {native_pid=Native.Id,pid=Process.GetCurrentProcess().Id,initialized=Initialized};
                        } else {
                            string state=(string)binding["state"];
                            if(operation=="cede") {
                                if(state!="returning") throw new InvalidOperationException("Binding is not returning");
                                result=await Cede(thread);
                            } else if(operation=="allow") {
                                if(state!="external") throw new InvalidOperationException("Binding is not external");
                                lock(State) Blocked.Remove(thread);
                                bool restored=await Restore(thread,true);
                                Audit(new {type="desktop_gate_released",threadId=thread,restored=restored}); result=new {native_id=thread,allowed=true,restored=restored};
                            } else throw new InvalidOperationException("Unknown control operation");
                        }
                        var envelope=new Dictionary<string,object> {{"ok",true},{"result",result},{"nonce",Nonce},{"native_id",replyNative},{"scope_id",replyScope},{"initialized",Initialized}};
                        if(operation=="cede") {envelope["released"]=true;envelope["loaded"]=false;envelope["already_absent"]=Parse(Serialize(result))["already_absent"];}
                        reply=envelope;
                    } catch(Exception error) { reply=new {ok=false,error=error.Message,nonce=Nonce,native_id=replyNative,scope_id=replyScope,busy=error is Busy,released=false}; }
                    try { await writer.WriteLineAsync(Serialize(reply)); }
                    catch(IOException) { /* A caller timing out does not stop the native desktop. */ }
                }
            }
        } finally { listener.Stop(); }
    }
    static Dictionary<string,object> Binding(string thread) {
        Guid identity; if(!Guid.TryParse(thread,out identity))return null;
        var path=Path.Combine(BridgeRoot,"bindings",thread+".json");
        if(!File.Exists(path))return null;
        var binding=Parse(File.ReadAllText(path));
        if((string)binding["native_id"]!=thread || !String.Equals(Path.GetFullPath((string)binding["native_home"]).TrimEnd('\\'),Path.GetFullPath(Env("CODEX_HOME")).TrimEnd('\\'),StringComparison.OrdinalIgnoreCase))return null;
        return binding;
    }
    static bool IsGated(string thread) {
        var binding=Binding(thread);
        return binding!=null && ((string)binding["state"]=="crew" || (string)binding["state"]=="returning" || Blocked.Contains(thread));
    }
    static async Task<object> Cede(string thread) {
        if(String.IsNullOrEmpty(thread)) throw new InvalidOperationException("threadId required");
        lock(State) {
            Blocked.Add(thread); Closed.Remove(thread);
            if(Restoring.Contains(thread)) { Blocked.Remove(thread); throw new Busy("Native cold resume is still in flight"); }
            if(Loading.Values.Contains(thread)) { Blocked.Remove(thread); throw new Busy("Thread write request is still in flight"); }
        }
        var started=Stopwatch.StartNew();
        // Non-owner app-server instances need no unsubscribe and cannot veto a
        // handoff merely because another thread or global prompt is busy.
        var before=await LoadedThreads(started);
        bool alreadyAbsent=!Ids(before).Contains(thread);
        if(alreadyAbsent) {
            lock(State) Blocked.Remove(thread);
            return new {threadId=thread,released=true,thread_closed=false,already_absent=true,loaded=before,elapsed_ms=started.ElapsedMilliseconds,native_alive=!Native.HasExited};
        }
        lock(State) {
            if(NativeRequests.Values.Any(id => id==thread || id=="*")) { Blocked.Remove(thread); throw new Busy("Native server request is still awaiting a response"); }
        }
        // Preserve the gate on uncertainty; failure is never reported as successful release.
        var read=await Request("thread/read",new {threadId=thread,includeTurns=false},Remaining(started));
        var record=read["thread"] as Dictionary<string,object>;
        var status=record["status"] as Dictionary<string,object>;
        var kind=(string)status["type"];
        if(kind != "idle" && kind != "notLoaded") {
            lock(State) { Blocked.Remove(thread); }
            throw new Busy("Native thread status is " + kind);
        }
        lock(State) {
            if(NativeRequests.Values.Any(id => id==thread || id=="*")) { Blocked.Remove(thread); throw new Busy("Native server request arrived before unsubscribe"); }
        }
        await Request("thread/unsubscribe",new {threadId=thread},Remaining(started));
        for(int i=0;i<160;i++) {
            var after=await LoadedThreads(started);
            bool closed; lock(State) closed=Closed.Contains(thread);
            if(!Ids(after).Contains(thread) && (closed || alreadyAbsent)) {
                var receipt=ResumeReceipt(thread);
                if(receipt!=null && (string)receipt["owner_nonce"]==Nonce)SaveResume(thread,receipt["params"] as Dictionary<string,object>,"yielded");
                var result=new {threadId=thread,released=true,thread_closed=closed,already_absent=alreadyAbsent,loaded=after,elapsed_ms=started.ElapsedMilliseconds,native_alive=!Native.HasExited};
                Audit(new {type="cede_completed",result=result}); return result;
            }
            await Task.Delay(50);
        }
        throw new TimeoutException("Thread did not close; desktop remains gated and ownership is unresolved");
    }
    static Dictionary<string,object> DesktopParameters(string thread, Dictionary<string,object> original) {
        var parameters=Parse(Serialize(original)); var binding=Binding(thread);
        if(binding!=null && (string)binding["state"]=="external" && binding.ContainsKey("desktop_instructions")) {
            var instructions=binding["desktop_instructions"] as string;
            if(!String.IsNullOrWhiteSpace(instructions)) {
                var text=parameters.ContainsKey("developerInstructions") ? parameters["developerInstructions"] as string : null;
                parameters["developerInstructions"]=String.IsNullOrEmpty(text) ? instructions : text+"\n\n"+instructions;
            }
        }
        if(binding!=null && (string)binding["state"]=="external" && binding.ContainsKey("desktop_config_overrides")) {
            var overrides=binding["desktop_config_overrides"] as Dictionary<string,object>;
            if(overrides!=null && overrides.Count>0) {
                var config=parameters.ContainsKey("config") ? parameters["config"] as Dictionary<string,object> : null;
                if(config==null)config=new Dictionary<string,object>();
                MergeConfig(config,overrides,new string[0]);parameters["config"]=config;
            }
        }
        return parameters;
    }
    static void MergeConfig(Dictionary<string,object> config,Dictionary<string,object> overrides,string[] prefix) {
        foreach(var entry in overrides) {
            var path=prefix.Concat(entry.Key.Split('.')).ToArray();
            var nested=entry.Value as Dictionary<string,object>;
            if(nested!=null){MergeConfig(config,nested,path);continue;}
            // Native config accepts nested tables and dotted keys. Keep both
            // representations consistent so an existing dotted true cannot
            // undo the member's existing no-delegation boundary.
            config[String.Join(".",path)]=entry.Value;
            var parent=config;
            for(int index=0;index<path.Length-1;index++) {
                object value;Dictionary<string,object> child=null;
                if(parent.TryGetValue(path[index],out value))child=value as Dictionary<string,object>;
                if(child==null){child=new Dictionary<string,object>();parent[path[index]]=child;}
                parent=child;
            }
            parent[path[path.Length-1]]=entry.Value;
        }
    }
    static string ResultThread(Dictionary<string,object> result) {
        var thread=result!=null && result.ContainsKey("thread") ? result["thread"] as Dictionary<string,object> : null;
        return thread!=null && thread.ContainsKey("id") ? thread["id"] as string : null;
    }
    static string ResumePath(string thread) {return Path.Combine(BridgeRoot,"thread-state",thread+".json");}
    static Dictionary<string,object> ResumeReceipt(string thread) {
        var binding=Binding(thread); if(binding==null || !File.Exists(ResumePath(thread)))return null;
        var saved=Parse(File.ReadAllText(ResumePath(thread)));
        if((string)saved["native_id"]!=thread || (string)saved["scope_id"]!=(string)binding["scope_id"] || !String.Equals((string)saved["native_home"],(string)binding["native_home"],StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("Saved desktop resume identity does not match the binding");
        return saved;
    }
    static void SaveResume(string thread,Dictionary<string,object> parameters,string state) {
        var binding=Binding(thread); if(binding==null)return;
        var saved=new {native_id=thread,scope_id=binding["scope_id"],native_home=binding["native_home"],owner_nonce=Nonce,owner_pid=Process.GetCurrentProcess().Id,owner_start=Process.GetCurrentProcess().StartTime.ToUniversalTime().Ticks,state=state,@params=parameters};
        string path=ResumePath(thread), temporary=path+"."+Nonce+".tmp";
        Directory.CreateDirectory(Path.GetDirectoryName(path));
        File.WriteAllText(temporary,Serialize(saved),new UTF8Encoding(false));
        if(File.Exists(path))File.Replace(temporary,path,null);else File.Move(temporary,path);
    }
    static bool OwnerAlive(Dictionary<string,object> saved) {
        try { using(var process=Process.GetProcessById(Convert.ToInt32(saved["owner_pid"])))return !process.HasExited && process.StartTime.ToUniversalTime().Ticks==Convert.ToInt64(saved["owner_start"]); }
        catch(ArgumentException){return false;}catch(InvalidOperationException){return false;}
    }
    static async Task<bool> Restore(string thread,bool ownerOnly) {
        var binding=Binding(thread); if(binding==null || (string)binding["state"]!="external")return false;
        var saved=ResumeReceipt(thread); if(saved==null)return false;
        bool same=(string)saved["owner_nonce"]==Nonce;
        // Background desktop connections must not race the visible owner's
        // cold resume. After process restart, only a new explicit user turn on
        // the actual Desktop connection may claim the dead owner's receipt.
        if(!same && (ownerOnly || OwnerAlive(saved)))return false;
        var started=Stopwatch.StartNew();
        Directory.CreateDirectory(Path.GetDirectoryName(ResumePath(thread)));
        using(var lease=new FileStream(ResumePath(thread)+".lock",FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None)) {
            lock(State) Restoring.Add(thread);
            try {
                saved=ResumeReceipt(thread);
                if((string)saved["owner_nonce"]!=Nonce && OwnerAlive(saved))return false;
                if(Ids(await LoadedThreads(started)).Contains(thread))return false;
                if((string)Binding(thread)["state"]!="external")throw new InvalidOperationException("Desktop control changed before cold resume");
                var original=saved["params"] as Dictionary<string,object>;
                var result=await Request("thread/resume",DesktopParameters(thread,original),Remaining(started));
                if(ResultThread(result)!=thread)throw new InvalidOperationException("Native cold resume returned a different thread; nothing was replayed");
                SaveResume(thread,original,"loaded");return true;
            } finally {lock(State) Restoring.Remove(thread);}
        }
    }
    static async Task<Dictionary<string,object>> LoadedThreads(Stopwatch started) {
        var all=new List<string>(); string cursor=null;
        do {
            var parameters=new Dictionary<string,object>();if(cursor!=null)parameters["cursor"]=cursor;
            var page=await Request("thread/loaded/list",parameters,Remaining(started));all.AddRange(Ids(page));
            cursor=page.ContainsKey("nextCursor") ? page["nextCursor"] as string : null;
        } while(cursor!=null);
        return new Dictionary<string,object>{{"data",all.ToArray()},{"nextCursor",null}};
    }
    static int Remaining(Stopwatch started) {
        int left=10000-(int)started.ElapsedMilliseconds;
        if(left<=0)throw new TimeoutException("Native cede exceeded its 10-second deadline");
        return Math.Min(left,5000);
    }
    static IEnumerable<string> Ids(Dictionary<string,object> result) { return ((IEnumerable)result["data"]).Cast<object>().Select(x => x as string); }
    static void Audit(object value) { var path=Env("CREW_CODEX_BRIDGE_AUDIT"); if(String.IsNullOrEmpty(path))return;
        lock(AuditLock) File.AppendAllText(path,Serialize(value)+"\n",new UTF8Encoding(false)); }
    static string Quote(string argument) {
        if(argument.Length>0 && argument.IndexOfAny(new[]{' ','\t','"'})<0)return argument;
        var b=new StringBuilder("\""); int slash=0;
        foreach(char c in argument) { if(c=='\\'){slash++;continue;} if(c=='"'){b.Append('\\',slash*2+1);b.Append('"');slash=0;continue;} b.Append('\\',slash);slash=0;b.Append(c); }
        b.Append('\\',slash*2);b.Append('"');return b.ToString();
    }
}
