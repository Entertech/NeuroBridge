#include "NumCpp.hpp"
#include "Data.h"

namespace basic
{
    namespace dsp
    {
        EEGPower::EEGPower()
        {
            power = 0;
            alpha = 0;
            beta = 0;
            theta = 0;
            delta = 0;
            gamma = 0;
            highBeta = 0;
            lowBeta = 0;
        }

        EEGPower::~EEGPower()
        {
        }

        bool EEGPower::validate()
        {
            if (alpha > 0 && beta > 0 && theta > 0 && delta > 0 && gamma > 0)
                return true;
                
            return false;
        }

        double EEGPower::powerDB()
        {
            if (power > 0)
            {
                auto temp = 20 * nc::log10(power);
                auto array = nc::NdArray<double>{temp, 0};
                return nc::max(array).at(0);
            }
            return 0;
        }

        double EEGPower::alphaDB()
        {
            if (alpha > 0)
            {
                auto temp = 20 * nc::log10(alpha);
                auto array = nc::NdArray<double>{temp, 0};
                return nc::max(array).at(0);
            }
            return 0;
        }

        double EEGPower::betaDB()
        {
            if (beta > 0)
            {
                auto temp = 20 * nc::log10(beta);
                auto array = nc::NdArray<double>{temp, 0};
                return nc::max(array).at(0);
            }
            return 0;
        }

        double EEGPower::thetaDB()
        {
            if (theta > 0)
            {
                auto temp = 20 * nc::log10(theta);
                auto array = nc::NdArray<double>{temp, 0};
                return nc::max(array).at(0);
            }
            return 0;
        }

        double EEGPower::deltaDB()
        {
            if (delta > 0)
            {
                auto temp = 20 * nc::log10(delta);
                auto array = nc::NdArray<double>{temp, 0};
                return nc::max(array).at(0);
            }
            return 0;
        }

        double EEGPower::gammaDB()
        {
            if (gamma > 0)
            {
                auto temp = 20 * nc::log10(gamma);
                auto array = nc::NdArray<double>{temp, 0};
                return nc::max(array).at(0);
            }
            return 0;
        }

        double EEGPower::highBetaDB()
        {
            if (highBeta > 0)
            {
                auto temp = 20 * nc::log10(highBeta);
                auto array = nc::NdArray<double>{temp, 0};
                return nc::max(array).at(0);
            }
            return 0;
        }

        double EEGPower::lowBetaDB()
        {
            if (lowBeta > 0)
            {
                auto temp = 20 * nc::log10(lowBeta);
                auto array = nc::NdArray<double>{temp, 0};
                return nc::max(array).at(0);
            }
            return 0;
        }

        double EEGPower::totalPower()
        {
            return alpha + beta + theta + delta + gamma;
        }

        double EEGPower::alphaNorm()
        {
            if (validate())
            {
                auto total = totalPower();
                return alpha / total;
            }
            return 0;
        }

        double EEGPower::betaNorm()
        {
            if (validate())
            {
                auto total = totalPower();
                return beta / total;
            }
            return 0;
        } 
        double EEGPower::thetaNorm()
        {
            if (validate())
            {
                auto total = totalPower();
                return theta / total;
            }
            return 0;
        }
        double EEGPower::deltaNorm()
        {
            if (validate())
            {
                auto total = totalPower();
                return delta / total;
            }
            return 0;
        }
        double EEGPower::gammaNorm()
        {
            if (validate())
            {
                auto total = totalPower();
                return gamma / total;
            }
            return 0;
        }

        HRPower::HRPower()
        {
            power = 0;
            hf = 0;
            lf = 0;
            vlf = 0;
        }

        HRPower::~HRPower() {}

        double HRPower::hfn()
        {
            if (power > 0)
            {
                return 100 * hf / (power - vlf); //# 高频成分标准值
            }
            return 0;
        }

        double HRPower::lfn()
        {
            if (power > 0)
            {
                return 100 * lf / (power - vlf);
            }
            return 0;
        }

        double HRPower::freqRate()
        {
            if (hf > 0)
            {
                return lf / hf;
            }
            return 0;
        }

    } // namespace data

} // namespace basic
