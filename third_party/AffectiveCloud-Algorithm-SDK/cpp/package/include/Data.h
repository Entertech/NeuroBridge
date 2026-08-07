#ifndef DSP_DATA_HEADER_FILE_GUARD 
#define DSP_DATA_HEADER_FILE_GUARD

namespace basic
{
    namespace dsp
    {
        enum EEGQuality
        {
            NONE = 0,
            POOR,
            GOOD
        };
        enum HRQuality
        {
            INVALID = 0,
            VALID
        };

        enum BCGQuality
        {
            BCG_NONE = 0,
            BCG_POOR,
            BCG_NORM
        };

        enum RWQuality
        {
            RW_NONE = 0,
            RW_POOR,
            RW_NORM
        };



        class EEGPower
        {
        public:
            double power;
            double alpha;
            double beta;
            double theta;
            double delta;
            double gamma;
            double highBeta;
            double lowBeta;

            double powerDB();
            double alphaDB();
            double betaDB();
            double thetaDB();
            double deltaDB();
            double gammaDB();
            double highBetaDB();
            double lowBetaDB();

            double alphaNorm();
            double betaNorm();
            double thetaNorm();
            double deltaNorm();
            double gammaNorm();
            
            EEGPower();
            ~EEGPower();
        private:
            bool validate();
            double totalPower();
        };

        class HRPower
        {
        public:
            double power; // 总功率
            double hf;    // 高频成分（0.15~0.4Hz）
            double lf;    // 低频成分（0.04~0.15Hz）
            double vlf;   // 超低频成分（0.003~0.04Hz）
            double lfn();
            double freqRate();
            double hfn();
            HRPower();
            ~HRPower();
        };
    };
}
#endif