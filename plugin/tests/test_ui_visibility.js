"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),{create}=require("../crew/web/ui-visibility.js");
test("independent portal owners compose reading obstructions and unregister without changing another feature",()=>{
  const visibility=create();let comments=false,materials=false;const removeComments=visibility.register("comments",()=>comments);visibility.register("materials",()=>materials);
  assert.equal(visibility.blocked(),false);comments=true;assert.equal(visibility.blocked(),true);comments=false;materials=true;assert.equal(visibility.blocked(),true);
  removeComments();assert.equal(visibility.blocked(),true);materials=false;assert.equal(visibility.blocked(),false);visibility.destroy();assert.deepEqual(visibility.owners(),[]);
});
test("duplicate owners fail and stale unregistration cannot remove a remounted portal",()=>{
  const visibility=create(),old=visibility.register("comments",()=>true);assert.throws(()=>visibility.register("comments",()=>false),/already registered/);old();visibility.register("comments",()=>true);old();assert.equal(visibility.blocked(),true);
});
