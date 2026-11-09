use media_domain::MediaError;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct Rational {
    pub numerator: i64,
    pub denominator: i64,
}

impl Rational {
    pub fn parse(value: &str) -> Result<Self, MediaError> {
        let (n, d) = value.split_once('/').ok_or_else(invalid)?;
        let numerator = n.parse::<i64>().map_err(|_| invalid())?;
        let denominator = d.parse::<i64>().map_err(|_| invalid())?;
        if numerator <= 0 || denominator <= 0 {
            return Err(invalid());
        }
        Ok(Self {
            numerator,
            denominator,
        })
    }

    pub fn micros(self, ticks: i64) -> Result<i64, MediaError> {
        let value = i128::from(ticks)
            .checked_mul(i128::from(self.numerator))
            .and_then(|value| value.checked_mul(1_000_000))
            .ok_or_else(invalid)?
            / i128::from(self.denominator);
        i64::try_from(value).map_err(|_| invalid())
    }
}

pub(crate) fn seconds_arg(us: i64) -> String {
    let value = i128::from(us);
    let sign = if value < 0 { "-" } else { "" };
    let value = value.abs();
    format!("{sign}{}.{:06}", value / 1_000_000, value % 1_000_000)
}

fn invalid() -> MediaError {
    MediaError::new("INVALID_INPUT", "Media time base or timestamp is invalid.")
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rational_timestamps_do_not_use_float_seconds() {
        let time = Rational::parse("1/30000").expect("valid timebase");
        assert_eq!(time.micros(88000).expect("timestamp"), 2_933_333);
        assert_eq!(time.micros(-2000).expect("timestamp"), -66_666);
        assert_eq!(seconds_arg(1_933_333), "1.933333");
        assert!(Rational::parse("1/0").is_err());
        assert!(Rational::parse("NaN").is_err());
        assert!(
            Rational {
                numerator: i64::MAX,
                denominator: 1
            }
            .micros(i64::MAX)
            .is_err()
        );
    }
}
