#![cfg(target_os = "macos")]
#[test]
fn effective_uid_matches_host_id() {
    let out = std::process::Command::new("/usr/bin/id")
        .arg("-u")
        .output()
        .unwrap();
    assert!(out.status.success());
    let expected = std::str::from_utf8(&out.stdout)
        .unwrap()
        .trim()
        .parse::<u32>()
        .unwrap();
    assert_eq!(media_ai::effective_uid(), expected);
}
