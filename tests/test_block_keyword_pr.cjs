const assert = require("node:assert/strict");
const test = require("node:test");
const createKeywordPR = require("../scripts/block-keyword-pr.cjs");

function fixture(keyword = "新增关键词", content = "原有词\n") {
  const calls = [];
  let branchExists = false;
  let pull = null;
  let file = content;
  const context = {
    repo: {owner: "example", repo: "archive"},
    payload: {
      action: "opened",
      repository: {default_branch: "master"},
      issue: {number: 12, title: `[Block] ${keyword}`,
        body: `<!-- forum-archive-block:v1 -->\n${JSON.stringify({keyword})}`},
    },
  };
  const github = {rest: {
    pulls: {
      list: async params => ({data: pull ? [pull] : []}),
      create: async params => {
        calls.push(["pull", params]);
        pull = {html_url: "https://github.com/example/archive/pull/34"};
        return {data: pull};
      },
    },
    git: {
      getRef: async params => {
        if (params.ref === "heads/block/issue-12" && !branchExists) {
          throw Object.assign(new Error("Not found"), {status: 404});
        }
        return {data: {object: {sha: "base-sha"}}};
      },
      createRef: async params => { calls.push(["branch", params]); branchExists = true; },
    },
    repos: {
      getContent: async params => {
        calls.push(["read", params]);
        return {data: {type: "file", encoding: "base64", sha: "file-sha", content: Buffer.from(file).toString("base64")}};
      },
      createOrUpdateFileContents: async params => {
        calls.push(["write", params]);
        file = Buffer.from(params.content, "base64").toString("utf8");
      },
    },
  }};
  return {github, context, core: {info() {}}, calls, content: () => file};
}

test("appends exactly one line on a separate branch and opens a PR to master", async () => {
  const state = fixture('读者 "建议" <script> 😀', "原有词");
  await createKeywordPR(state);
  assert.equal(state.content(), '原有词\n读者 "建议" <script> 😀\n');
  assert.equal(state.calls.find(([kind]) => kind === "branch")[1].ref, "refs/heads/block/issue-12");
  const write = state.calls.find(([kind]) => kind === "write")[1];
  assert.equal(write.path, "blacklist/keywords.txt");
  assert.equal(write.branch, "block/issue-12");
  const pull = state.calls.find(([kind]) => kind === "pull")[1];
  assert.equal(pull.base, "master");
  assert.equal(pull.head, "block/issue-12");
  assert.match(pull.body, /Closes #12/);
  assert.deepEqual(state.calls.filter(([kind]) => kind !== "read").map(([kind]) => kind), ["branch", "write", "pull"]);
});

test("does nothing for unrelated issues", async () => {
  const state = fixture();
  state.context.payload.issue.title = "普通问题";
  await createKeywordPR(state);
  assert.deepEqual(state.calls, []);
});

test("rejects malformed or multiline input before writing", async () => {
  for (const keyword of ["", "   ", "x".repeat(81), "第一行\n第二行", "词\u0000", "词\u200b", " 尾部 ", 123]) {
    const state = fixture(keyword);
    await assert.rejects(createKeywordPR(state), /关键词/);
    assert.deepEqual(state.calls, []);
  }
  for (const body of ["not JSON", '<!-- forum-archive-block:v1 -->\n{"keyword":"词","path":"other"}']) {
    const state = fixture();
    state.context.payload.issue.body = body;
    await assert.rejects(createKeywordPR(state));
    assert.deepEqual(state.calls, []);
  }
});

test("accepts 80 Unicode characters and detects normalized covered words", async () => {
  const state = fixture("😀".repeat(80));
  await createKeywordPR(state);
  assert.ok(state.content().endsWith("😀".repeat(80) + "\n"));
  for (const keyword of ["原有词", "包含原 有 词的文字", "ＡＢＣ"]) {
    const duplicate = fixture(keyword, "原有词\nabc\n");
    await createKeywordPR(duplicate);
    assert.ok(duplicate.calls.every(([kind]) => kind === "read"));
  }
});

test("rerunning an issue does not create a second PR or append twice", async () => {
  const state = fixture();
  await createKeywordPR(state);
  const calls = state.calls.length;
  await createKeywordPR(state);
  assert.equal(state.calls.length, calls);
  assert.equal(state.content(), "原有词\n新增关键词\n");
});

test("recovers when PR creation failed after the file was committed", async () => {
  const state = fixture();
  const create = state.github.rest.pulls.create;
  state.github.rest.pulls.create = async () => { throw new Error("API unavailable"); };
  await assert.rejects(createKeywordPR(state), /API unavailable/);
  state.github.rest.pulls.create = create;
  await createKeywordPR(state);
  assert.equal(state.calls.filter(([kind]) => kind === "write").length, 1);
  assert.equal(state.calls.filter(([kind]) => kind === "pull").length, 1);
});

test("recovers when the first file update failed after branch creation", async () => {
  const state = fixture();
  const write = state.github.rest.repos.createOrUpdateFileContents;
  state.github.rest.repos.createOrUpdateFileContents = async () => { throw new Error("API unavailable"); };
  await assert.rejects(createKeywordPR(state), /API unavailable/);
  state.github.rest.repos.createOrUpdateFileContents = write;
  await createKeywordPR(state);
  assert.equal(state.calls.filter(([kind]) => kind === "branch").length, 1);
  assert.equal(state.content(), "原有词\n新增关键词\n");
});
