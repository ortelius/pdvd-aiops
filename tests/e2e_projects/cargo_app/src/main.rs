use clap::Parser;
use serde::{Deserialize, Serialize};

#[derive(Parser, Debug)]
#[command(name = "e2e-cargo-app", about = "E2E test Rust CLI")]
struct Cli {
    /// Name to greet
    #[arg(short, long, default_value = "World")]
    name: String,

    /// Output as JSON
    #[arg(short, long)]
    json: bool,
}

#[derive(Serialize, Deserialize, Debug)]
struct Greeting {
    message: String,
    name: String,
}

fn create_greeting(name: &str) -> Greeting {
    Greeting {
        message: format!("Hello, {}!", name),
        name: name.to_string(),
    }
}

fn main() {
    let cli = Cli::parse();
    let greeting = create_greeting(&cli.name);

    if cli.json {
        let json = serde_json::to_string_pretty(&greeting).unwrap();
        println!("{}", json);
    } else {
        println!("{}", greeting.message);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_create_greeting() {
        let g = create_greeting("Rust");
        assert_eq!(g.name, "Rust");
        assert_eq!(g.message, "Hello, Rust!");
    }

    #[test]
    fn test_greeting_serialization() {
        let g = create_greeting("Test");
        let json = serde_json::to_string(&g).unwrap();
        assert!(json.contains("Hello, Test!"));

        let deserialized: Greeting = serde_json::from_str(&json).unwrap();
        assert_eq!(deserialized.name, "Test");
    }

    #[test]
    fn test_greeting_default_world() {
        let g = create_greeting("World");
        assert_eq!(g.message, "Hello, World!");
    }
}
